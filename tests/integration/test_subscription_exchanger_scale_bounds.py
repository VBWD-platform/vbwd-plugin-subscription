"""Integration: S89 scale bounds for the subscription plans/addons exchangers.

These tests assert ALGORITHMIC bounds (not wall-clock), so a regression fails CI
without a 100k run:

* **import flush count** — importing N plans must NOT flush once per plan
  (an analogue of the shop per-row flush regression). Import flushes only at
  core's chunk boundaries.
* **reset statement count** — resetting M seeded plans must issue a BOUNDED
  number of SQL statements (set-based deletes of the M2M link tables + the
  parent), NOT O(M); the plans AND their link rows are removed while
  non-loadtest data is untouched.

Engineering requirements (binding, restated): TDD-first; DevOps-first (cold
local + CI via the shared ``db`` fixture, no raw SQL); SOLID/DI/DRY; Liskov;
no overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin
subscription --full``.
"""
from contextlib import contextmanager

from sqlalchemy import event

from vbwd.models.enums import BillingPeriod
from vbwd.services.data_exchange.base_model_exchanger import EXPORT_CHUNK_SIZE
from vbwd.services.data_exchange.envelope import build_envelope

from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from plugins.subscription.subscription.models.tarif_plan_category import (
    TarifPlanCategory,
    tarif_plan_category_plans,
)
from plugins.subscription.subscription.services.data_exchange.subscription_exchangers import (  # noqa: E501
    build_subscription_exchangers,
)

_SEED_CATEGORY_SLUG = "loadtest-subscription_plans-cat"

# A small FIXED ceiling on per-table SELECTs during an import: the two cache
# preloads plus a handful of relationship lazy-loads that do NOT scale with the
# row count. Well under any per-row (~N) regression, so a return to per-row
# queries still trips the assertion.
_MAX_FIXED_SELECTS = 20


def _plans_exchanger(session):
    return {
        exchanger.entity_key: exchanger
        for exchanger in build_subscription_exchangers(session)
    }["subscription_plans"]


@contextmanager
def _count_flushes(session):
    counter = {"count": 0}
    original_flush = session.flush

    def _counting_flush(*args, **kwargs):
        counter["count"] += 1
        return original_flush(*args, **kwargs)

    session.flush = _counting_flush
    try:
        yield counter
    finally:
        session.flush = original_flush


@contextmanager
def _record_statements(engine):
    statements = []

    def _on_execute(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _on_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _on_execute)


def _count_deletes_for(statements, table_name):
    return sum(
        1
        for statement in statements
        if "DELETE" in statement.upper() and table_name in statement
    )


def _count_selects_for(statements, table_name):
    return sum(
        1
        for statement in statements
        if statement.upper().lstrip().startswith("SELECT") and table_name in statement
    )


class TestPlansImportFlushBound:
    def test_plans_import_flushes_per_batch_not_per_row(self, db):
        row_count = 500
        exchanger = _plans_exchanger(db.session)
        exchanger._ensure_seed_prerequisite()
        db.session.commit()

        rows = [
            {
                "slug": f"loadtest-subscription_plans-{index}",
                "name": f"Load-test plan {index}",
                "price": 19.0,
                "billing_period": "MONTHLY",
                "features": [],
                "trial_days": 0,
                "is_active": True,
                "sort_order": index,
                "category_slugs": [_SEED_CATEGORY_SLUG],
            }
            for index in range(row_count)
        ]
        payload = build_envelope("subscription_plans", rows, instance="test")

        with _count_flushes(db.session) as flushes:
            result = exchanger.import_(payload, mode="upsert", dry_run=False)

        assert result.created == row_count
        # The base import does NOT flush per row; the M2M reapply must not add a
        # per-row flush either. Bound generously to a small multiple of the chunk
        # count.
        max_expected_flushes = (row_count // EXPORT_CHUNK_SIZE) + 5
        assert flushes["count"] <= max_expected_flushes, (
            f"expected <= {max_expected_flushes} flushes for {row_count} plans, "
            f"got {flushes['count']} (regressed to per-row flush?)"
        )


class TestPlansImportSelectBound:
    """The plans upsert import must NOT issue per-row SELECTs.

    The old ``_resolve_related`` did one category SELECT per slug per row and the
    base ``_import_row`` did one ``find_by_natural_key`` SELECT per row — ~2N
    sequential round-trips. The fix preloads both into per-import caches so the
    SELECT count against ``tarif_plan`` / ``tarif_plan_category`` is bounded.
    """

    def test_cold_upsert_into_empty_table_is_o1_selects(self, db):
        row_count = 300
        exchanger = _plans_exchanger(db.session)
        exchanger._ensure_seed_prerequisite()
        db.session.commit()

        rows = [
            {
                "slug": f"loadtest-subscription_plans-{index}",
                "name": f"Load-test plan {index}",
                "price": 19.0,
                "billing_period": "MONTHLY",
                "features": [],
                "trial_days": 0,
                "is_active": True,
                "sort_order": index,
                "category_slugs": [_SEED_CATEGORY_SLUG],
            }
            for index in range(row_count)
        ]
        payload = build_envelope("subscription_plans", rows, instance="test")

        import_exchanger = _plans_exchanger(db.session)
        engine = db.session.get_bind()
        with _record_statements(engine) as statements:
            result = import_exchanger.import_(payload, mode="upsert", dry_run=False)

        assert result.created == row_count
        category_selects = _count_selects_for(
            statements, TarifPlanCategory.__tablename__
        )
        plan_selects = _count_selects_for(statements, TarifPlan.__tablename__)
        assert category_selects <= _MAX_FIXED_SELECTS, (
            f"expected O(1) category SELECTs, got {category_selects} for "
            f"{row_count} plans (regressed to per-row resolve_related?)"
        )
        assert plan_selects <= _MAX_FIXED_SELECTS, (
            f"expected O(1) existence SELECTs, got {plan_selects} for "
            f"{row_count} plans (regressed to per-row find_by_natural_key?)"
        )

    def test_upsert_into_populated_table_is_o1_selects(self, db):
        row_count = 300
        seed_exchanger = _plans_exchanger(db.session)
        seed_exchanger.bulk_seed(row_count)
        db.session.commit()
        assert (
            db.session.query(TarifPlan)
            .filter(TarifPlan.slug.like("loadtest-%"))
            .count()
            == row_count
        )

        rows = [
            {
                "slug": f"loadtest-subscription_plans-{index}",
                "name": f"Load-test plan {index} (v2)",
                "price": 20.0,
                "billing_period": "MONTHLY",
                "features": [],
                "trial_days": 0,
                "is_active": True,
                "sort_order": index,
                "category_slugs": [_SEED_CATEGORY_SLUG],
            }
            for index in range(row_count)
        ]
        payload = build_envelope("subscription_plans", rows, instance="test")

        import_exchanger = _plans_exchanger(db.session)
        engine = db.session.get_bind()
        with _record_statements(engine) as statements:
            result = import_exchanger.import_(payload, mode="upsert", dry_run=False)

        assert result.updated == row_count
        category_selects = _count_selects_for(
            statements, TarifPlanCategory.__tablename__
        )
        plan_selects = _count_selects_for(statements, TarifPlan.__tablename__)
        assert (
            category_selects <= _MAX_FIXED_SELECTS
        ), f"expected O(1) category SELECTs, got {category_selects}"
        assert plan_selects <= _MAX_FIXED_SELECTS, (
            f"expected O(1) existence SELECTs, got {plan_selects} for "
            f"{row_count} existing plans (existence not served from preload?)"
        )

    def test_unknown_non_seed_category_still_skips_with_error(self, db):
        exchanger = _plans_exchanger(db.session)
        exchanger._ensure_seed_prerequisite()
        db.session.commit()

        rows = [
            {
                "slug": "loadtest-subscription_plans-bad",
                "name": "Bad plan",
                "price": 19.0,
                "billing_period": "MONTHLY",
                "features": [],
                "trial_days": 0,
                "is_active": True,
                "sort_order": 0,
                "category_slugs": ["definitely-not-a-real-category"],
            }
        ]
        payload = build_envelope("subscription_plans", rows, instance="test")

        import_exchanger = _plans_exchanger(db.session)
        result = import_exchanger.import_(payload, mode="upsert", dry_run=False)

        assert result.created == 0
        assert len(result.errors) == 1
        assert "unknown category_slugs" in result.errors[0]["reason"]


class TestPlansResetStatementBound:
    def test_reset_is_bounded_statements_and_clears_links(self, db):
        seed_count = 200
        exchanger = _plans_exchanger(db.session)
        exchanger.bulk_seed(seed_count)
        db.session.commit()
        assert (
            db.session.query(TarifPlan)
            .filter(TarifPlan.slug.like("loadtest-%"))
            .count()
            == seed_count
        )

        reset_exchanger = _plans_exchanger(db.session)
        engine = db.session.get_bind()
        with _record_statements(engine) as statements:
            reset_exchanger.bulk_seed(0, reset=True)
            db.session.commit()

        delete_statements = [
            statement for statement in statements if "DELETE" in statement.upper()
        ]
        assert len(delete_statements) <= 12, (
            f"reset issued {len(delete_statements)} DELETE statements for "
            f"{seed_count} plans — expected a bounded set-based reset"
        )
        # The plan↔category link must be cleared by an EXPLICIT set-based DELETE
        # (the unindexed-cascade O(N²) path otherwise emits none).
        link_table = tarif_plan_category_plans.name
        assert _count_deletes_for(delete_statements, link_table) == 1, (
            f"expected exactly one set-based DELETE against {link_table}; "
            "a cascade-only reset would emit none and seq-scan it per row"
        )

        assert (
            db.session.query(TarifPlan)
            .filter(TarifPlan.slug.like("loadtest-%"))
            .count()
            == 0
        )
        link_rows = db.session.execute(tarif_plan_category_plans.select()).fetchall()
        assert link_rows == []

    def test_reset_spares_non_loadtest_plan_and_category(self, db):
        keeper_category = TarifPlanCategory(slug="real-plan-cat", name="Real")
        db.session.add(keeper_category)
        db.session.commit()
        keeper = TarifPlan(
            slug="real-plan-scale",
            name="Real",
            price=29.0,
            billing_period=BillingPeriod.MONTHLY,
        )
        keeper.categories = [keeper_category]
        db.session.add(keeper)
        db.session.commit()

        exchanger = _plans_exchanger(db.session)
        exchanger.bulk_seed(50)
        db.session.commit()

        reset_exchanger = _plans_exchanger(db.session)
        reset_exchanger.bulk_seed(0, reset=True)
        db.session.commit()

        assert (
            db.session.query(TarifPlan)
            .filter(TarifPlan.slug.like("loadtest-%"))
            .count()
            == 0
        )
        survivor = (
            db.session.query(TarifPlan)
            .filter(TarifPlan.slug == "real-plan-scale")
            .first()
        )
        assert survivor is not None
        assert [category.slug for category in survivor.categories] == ["real-plan-cat"]
