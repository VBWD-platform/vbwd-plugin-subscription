"""Integration: S89.1 load-test bulk seed for plans + add-ons (real PG).

Proves the seed overrides end-to-end through the repository layer (no raw SQL):

* ``subscription_plans`` ``bulk_seed(10)`` inserts 10 valid ``loadtest-`` plans
  linked to the one shared ``loadtest-`` category; they round-trip (export →
  wipe → import) with the category link intact; idempotent; ``--reset`` drops
  only the ``loadtest-`` rows + the orphaned category.
* ``subscription_addons`` ``bulk_seed(10)`` inserts 10 valid FLAT add-ons; they
  round-trip; ``--reset`` is clean.

Engineering requirements (binding, restated): TDD-first; DevOps-first (cold
local + CI via the shared ``db`` fixture, no raw SQL); SOLID/DI/DRY; Liskov;
no overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin
subscription --full``.
"""
from vbwd.models.enums import BillingPeriod
from vbwd.services.data_exchange.envelope import build_envelope
from vbwd.services.data_exchange.port import ExportSelector

from plugins.subscription.subscription.models.addon import AddOn
from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from plugins.subscription.subscription.models.tarif_plan_category import (
    TarifPlanCategory,
)
from plugins.subscription.subscription.services.data_exchange.subscription_exchangers import (
    build_subscription_exchangers,
)

_PLAN_CATEGORY_SLUG = "loadtest-subscription_plans-cat"


def _exchangers(session):
    return {
        exchanger.entity_key: exchanger
        for exchanger in build_subscription_exchangers(session)
    }


def _loadtest(session, model):
    return session.query(model).filter(model.slug.like("loadtest-%")).all()


class TestBulkSeedPlans:
    def test_seeds_valid_linked_plans(self, db):
        exchanger = _exchangers(db.session)["subscription_plans"]

        result = exchanger.bulk_seed(10)
        db.session.commit()

        assert result.created == 10
        plans = _loadtest(db.session, TarifPlan)
        assert len(plans) == 10
        for plan in plans:
            assert plan.price == exchanger._SEED_PLAN_PRICE
            assert plan.billing_period == BillingPeriod.MONTHLY
            assert [c.slug for c in plan.categories] == [_PLAN_CATEGORY_SLUG]

    def test_round_trips_with_category_link(self, db):
        exchanger = _exchangers(db.session)["subscription_plans"]
        exchanger.bulk_seed(10)
        db.session.commit()

        exported = exchanger.export(ExportSelector(ids=None), include_pii=False).rows
        loadtest_rows = [r for r in exported if r["slug"].startswith("loadtest-")]
        assert len(loadtest_rows) == 10
        assert all(r["category_slugs"] == [_PLAN_CATEGORY_SLUG] for r in loadtest_rows)

        db.session.query(TarifPlan).filter(TarifPlan.slug.like("loadtest-%")).delete(
            synchronize_session=False
        )
        db.session.commit()

        payload = build_envelope("subscription_plans", loadtest_rows, instance="test")
        result = exchanger.import_(payload, mode="upsert", dry_run=False)

        assert result.created == 10
        rebuilt = _loadtest(db.session, TarifPlan)
        assert len(rebuilt) == 10
        assert all(
            [c.slug for c in plan.categories] == [_PLAN_CATEGORY_SLUG]
            for plan in rebuilt
        )

    def test_idempotent(self, db):
        _exchangers(db.session)["subscription_plans"].bulk_seed(10)
        db.session.commit()

        result = _exchangers(db.session)["subscription_plans"].bulk_seed(10)
        db.session.commit()

        assert result.created == 0
        assert result.skipped == 10
        assert len(_loadtest(db.session, TarifPlan)) == 10
        categories = (
            db.session.query(TarifPlanCategory)
            .filter(TarifPlanCategory.slug == _PLAN_CATEGORY_SLUG)
            .all()
        )
        assert len(categories) == 1

    def test_reset_drops_only_loadtest(self, db):
        keeper = TarifPlan(
            slug="real-plan",
            name="Real",
            price=9.0,
            billing_period=BillingPeriod.MONTHLY,
        )
        db.session.add(keeper)
        db.session.commit()

        _exchangers(db.session)["subscription_plans"].bulk_seed(10)
        db.session.commit()

        result = _exchangers(db.session)["subscription_plans"].bulk_seed(
            5, reset=True
        )
        db.session.commit()

        assert result.deleted == 10
        assert result.created == 5
        assert len(_loadtest(db.session, TarifPlan)) == 5
        assert (
            db.session.query(TarifPlan).filter(TarifPlan.slug == "real-plan").first()
            is not None
        )


class TestBulkSeedAddons:
    def test_seeds_valid_flat_addons(self, db):
        exchanger = _exchangers(db.session)["subscription_addons"]

        result = exchanger.bulk_seed(10)
        db.session.commit()

        assert result.created == 10
        addons = _loadtest(db.session, AddOn)
        assert len(addons) == 10
        for addon in addons:
            assert addon.price == exchanger._SEED_ADDON_PRICE
            assert addon.billing_period == BillingPeriod.MONTHLY.value

    def test_round_trips(self, db):
        exchanger = _exchangers(db.session)["subscription_addons"]
        exchanger.bulk_seed(10)
        db.session.commit()

        exported = exchanger.export(ExportSelector(ids=None), include_pii=False).rows
        loadtest_rows = [r for r in exported if r["slug"].startswith("loadtest-")]
        assert len(loadtest_rows) == 10

        db.session.query(AddOn).filter(AddOn.slug.like("loadtest-%")).delete(
            synchronize_session=False
        )
        db.session.commit()

        payload = build_envelope("subscription_addons", loadtest_rows, instance="test")
        result = exchanger.import_(payload, mode="upsert", dry_run=False)

        assert result.created == 10
        assert len(_loadtest(db.session, AddOn)) == 10

    def test_reset_clean(self, db):
        exchanger = _exchangers(db.session)["subscription_addons"]
        exchanger.bulk_seed(10)
        db.session.commit()

        result = _exchangers(db.session)["subscription_addons"].bulk_seed(
            0, reset=True
        )
        db.session.commit()

        assert result.deleted == 10
        assert len(_loadtest(db.session, AddOn)) == 0
