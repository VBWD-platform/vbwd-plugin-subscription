"""Integration: S62 slug-carried links for subscription exchangers (real PG).

S62 **adds** a ``subscription_categories`` exchanger and **upgrades** the
existing ``subscription_plans`` / ``subscription_addons`` exchangers so their
FK / M2M links travel by **slug** (the core ``fk_natural_key_map`` is
export-only, so each link-carrying exchanger is a thin subclass that resolves
the slug back to the local id on import — skip-with-error on an unknown slug).

Covered:

* ``subscription_categories`` round-trips by ``slug``; the self-referential
  ``parent`` is carried + resolved by ``parent_slug``.
* A full graph (category + plan-in-category + addon-bound-to-plan) exports and
  re-imports into a wiped DB with **all links resolved by slug**; price /
  billing_period / features / trial_days are preserved.
* An unknown link slug produces an **error row** and does not crash (Liskov).
* Upsert by slug (re-import updates, never duplicates).
* ``dry_run`` writes nothing.
* The new ``subscription_categories`` entity is registered under the ``sales``
  cluster with the reused ``plans.view`` / ``plans.manage`` perms.
* Idempotence: export → import on an unchanged DB is a no-op (only updates).

Data is seeded through the ORM session (no raw SQL); the shared ``db`` fixture
creates + drops the test DB.

Engineering requirements (binding, restated): TDD-first; DevOps-first (cold
local + CI via the shared ``db`` fixture, no raw SQL); SOLID (one exchanger per
entity, narrow ports); DI (session injected); DRY (delegate to the base
exchanger / existing models); Liskov (unknown link → error row, never crash);
clean code; no overengineering. Quality guard:
``bin/pre-commit-check.sh --plugin subscription --full``.
"""
import uuid

from vbwd.models.enums import BillingPeriod
from vbwd.services.data_exchange.envelope import build_envelope
from vbwd.services.data_exchange.port import (
    CLUSTER_SALES,
    ExportSelector,
)
from plugins.subscription.subscription.models.addon import AddOn
from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from plugins.subscription.subscription.models.tarif_plan_category import (
    TarifPlanCategory,
)
from plugins.subscription.subscription.services.data_exchange.subscription_exchangers import (  # noqa: E501
    build_subscription_exchangers,
)

PERM_PLANS_VIEW = "subscription.plans.view"
PERM_PLANS_MANAGE = "subscription.plans.manage"
PERM_ADDONS_MANAGE = "subscription.addons.manage"


def _exchangers(session):
    return {
        exchanger.entity_key: exchanger
        for exchanger in build_subscription_exchangers(session)
    }


def _unique_slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class TestCategoriesRoundTrip:
    def test_round_trip_by_slug(self, db):
        slug = _unique_slug("cat")
        db.session.add(
            TarifPlanCategory(
                name="Sales",
                slug=slug,
                description="Sales plans",
                is_single=False,
                sort_order=3,
            )
        )
        db.session.commit()

        exchanger = _exchangers(db.session)["subscription_categories"]
        rows = exchanger.export(ExportSelector(ids=[slug]), include_pii=False).rows
        assert rows and rows[0]["slug"] == slug
        assert rows[0]["is_single"] is False
        assert rows[0]["sort_order"] == 3

        db.session.query(TarifPlanCategory).filter(
            TarifPlanCategory.slug == slug
        ).delete()
        db.session.commit()

        payload = build_envelope("subscription_categories", rows, instance="test")
        result = exchanger.import_(payload, mode="upsert", dry_run=False)
        assert result.created == 1

        rebuilt = (
            db.session.query(TarifPlanCategory)
            .filter(TarifPlanCategory.slug == slug)
            .first()
        )
        assert rebuilt is not None
        assert rebuilt.name == "Sales"
        assert rebuilt.is_single is False
        assert rebuilt.sort_order == 3

    def test_parent_carried_by_slug(self, db):
        parent_slug = _unique_slug("parent")
        child_slug = _unique_slug("child")
        parent = TarifPlanCategory(name="Parent", slug=parent_slug)
        db.session.add(parent)
        db.session.commit()
        db.session.add(
            TarifPlanCategory(name="Child", slug=child_slug, parent_id=parent.id)
        )
        db.session.commit()

        exchanger = _exchangers(db.session)["subscription_categories"]
        rows = exchanger.export(
            ExportSelector(ids=[child_slug]), include_pii=False
        ).rows
        assert rows[0]["parent_slug"] == parent_slug
        assert "parent_id" not in rows[0]

        db.session.query(TarifPlanCategory).filter(
            TarifPlanCategory.slug == child_slug
        ).delete()
        db.session.commit()

        payload = build_envelope("subscription_categories", rows, instance="test")
        exchanger.import_(payload, mode="upsert", dry_run=False)
        rebuilt = (
            db.session.query(TarifPlanCategory)
            .filter(TarifPlanCategory.slug == child_slug)
            .first()
        )
        assert rebuilt is not None
        assert rebuilt.parent_id == parent.id

    def test_unknown_parent_slug_errors_without_crash(self, db):
        child_slug = _unique_slug("orphan")
        rows = [
            {
                "name": "Orphan",
                "slug": child_slug,
                "parent_slug": "no-such-parent",
            }
        ]
        exchanger = _exchangers(db.session)["subscription_categories"]
        payload = build_envelope("subscription_categories", rows, instance="test")
        result = exchanger.import_(payload, mode="upsert", dry_run=False)
        assert result.errors
        assert result.created == 0
        assert (
            db.session.query(TarifPlanCategory)
            .filter(TarifPlanCategory.slug == child_slug)
            .first()
            is None
        )


class TestPlanCategoryLink:
    def _seed_plan_in_category(self, db, plan_slug, category_slug):
        category = TarifPlanCategory(name="Cat", slug=category_slug)
        plan = TarifPlan(
            slug=plan_slug,
            name="Gold",
            description="Gold",
            price=19.0,
            billing_period=BillingPeriod.MONTHLY,
            trial_days=7,
            features=["a", "b"],
        )
        plan.categories.append(category)
        db.session.add_all([category, plan])
        db.session.commit()

    def test_plan_carries_category_slugs(self, db):
        plan_slug = _unique_slug("plan")
        category_slug = _unique_slug("cat")
        self._seed_plan_in_category(db, plan_slug, category_slug)

        exchanger = _exchangers(db.session)["subscription_plans"]
        rows = exchanger.export(ExportSelector(ids=[plan_slug]), include_pii=False).rows
        assert rows[0]["category_slugs"] == [category_slug]

    def test_plan_link_resolves_on_import(self, db):
        plan_slug = _unique_slug("plan")
        category_slug = _unique_slug("cat")
        self._seed_plan_in_category(db, plan_slug, category_slug)

        plans = _exchangers(db.session)["subscription_plans"]
        plan_rows = plans.export(
            ExportSelector(ids=[plan_slug]), include_pii=False
        ).rows

        db.session.query(TarifPlan).filter(TarifPlan.slug == plan_slug).delete()
        db.session.commit()

        payload = build_envelope("subscription_plans", plan_rows, instance="test")
        plans.import_(payload, mode="upsert", dry_run=False)

        rebuilt = (
            db.session.query(TarifPlan).filter(TarifPlan.slug == plan_slug).first()
        )
        assert rebuilt is not None
        assert rebuilt.trial_days == 7
        assert rebuilt.features == ["a", "b"]
        assert [c.slug for c in rebuilt.categories] == [category_slug]

    def test_plan_unknown_category_slug_errors(self, db):
        plan_slug = _unique_slug("plan")
        rows = [
            {
                "slug": plan_slug,
                "name": "Gold",
                "price": 19.0,
                "billing_period": BillingPeriod.MONTHLY.value,
                "trial_days": 0,
                "is_active": True,
                "sort_order": 0,
                "category_slugs": ["no-such-category"],
            }
        ]
        plans = _exchangers(db.session)["subscription_plans"]
        payload = build_envelope("subscription_plans", rows, instance="test")
        result = plans.import_(payload, mode="upsert", dry_run=False)
        assert result.errors
        assert (
            db.session.query(TarifPlan).filter(TarifPlan.slug == plan_slug).first()
            is None
        )


class TestAddonPlanLink:
    def _seed_addon_bound_to_plan(self, db, addon_slug, plan_slug):
        plan = TarifPlan(
            slug=plan_slug,
            name="Gold",
            price=19.0,
            billing_period=BillingPeriod.MONTHLY,
        )
        addon = AddOn(slug=addon_slug, name="Extra", price=5)
        addon.tarif_plans.append(plan)
        db.session.add_all([plan, addon])
        db.session.commit()

    def test_addon_carries_plan_slugs(self, db):
        addon_slug = _unique_slug("addon")
        plan_slug = _unique_slug("plan")
        self._seed_addon_bound_to_plan(db, addon_slug, plan_slug)

        exchanger = _exchangers(db.session)["subscription_addons"]
        rows = exchanger.export(
            ExportSelector(ids=[addon_slug]), include_pii=False
        ).rows
        assert rows[0]["tarif_plan_slugs"] == [plan_slug]

    def test_addon_link_resolves_on_import(self, db):
        addon_slug = _unique_slug("addon")
        plan_slug = _unique_slug("plan")
        self._seed_addon_bound_to_plan(db, addon_slug, plan_slug)

        addons = _exchangers(db.session)["subscription_addons"]
        rows = addons.export(ExportSelector(ids=[addon_slug]), include_pii=False).rows

        db.session.query(AddOn).filter(AddOn.slug == addon_slug).delete()
        db.session.commit()

        payload = build_envelope("subscription_addons", rows, instance="test")
        addons.import_(payload, mode="upsert", dry_run=False)
        rebuilt = db.session.query(AddOn).filter(AddOn.slug == addon_slug).first()
        assert rebuilt is not None
        assert [p.slug for p in rebuilt.tarif_plans] == [plan_slug]

    def test_addon_unknown_plan_slug_errors(self, db):
        addon_slug = _unique_slug("addon")
        rows = [
            {
                "slug": addon_slug,
                "name": "Extra",
                "price": 5,
                "billing_period": BillingPeriod.MONTHLY.value,
                "config": {},
                "is_active": True,
                "sort_order": 0,
                "tarif_plan_slugs": ["no-such-plan"],
            }
        ]
        addons = _exchangers(db.session)["subscription_addons"]
        payload = build_envelope("subscription_addons", rows, instance="test")
        result = addons.import_(payload, mode="upsert", dry_run=False)
        assert result.errors
        assert db.session.query(AddOn).filter(AddOn.slug == addon_slug).first() is None


class TestFullGraphRoundTrip:
    def test_graph_round_trips_with_links(self, db):
        category_slug = _unique_slug("cat")
        plan_slug = _unique_slug("plan")
        addon_slug = _unique_slug("addon")

        category = TarifPlanCategory(name="Cat", slug=category_slug)
        plan = TarifPlan(
            slug=plan_slug,
            name="Gold",
            price=29.0,
            billing_period=BillingPeriod.MONTHLY,
            trial_days=14,
            features=["x"],
        )
        plan.categories.append(category)
        addon = AddOn(slug=addon_slug, name="Extra", price=7)
        addon.tarif_plans.append(plan)
        db.session.add_all([category, plan, addon])
        db.session.commit()

        exchangers = _exchangers(db.session)
        cat_rows = (
            exchangers["subscription_categories"]
            .export(ExportSelector(ids=[category_slug]), include_pii=False)
            .rows
        )
        plan_rows = (
            exchangers["subscription_plans"]
            .export(ExportSelector(ids=[plan_slug]), include_pii=False)
            .rows
        )
        addon_rows = (
            exchangers["subscription_addons"]
            .export(ExportSelector(ids=[addon_slug]), include_pii=False)
            .rows
        )

        # Wipe the graph (children first to satisfy M2M cascades).
        db.session.query(AddOn).filter(AddOn.slug == addon_slug).delete()
        db.session.query(TarifPlan).filter(TarifPlan.slug == plan_slug).delete()
        db.session.query(TarifPlanCategory).filter(
            TarifPlanCategory.slug == category_slug
        ).delete()
        db.session.commit()

        # Import in dependency order: categories → plans → addons.
        rebuilt = _exchangers(db.session)
        rebuilt["subscription_categories"].import_(
            build_envelope("subscription_categories", cat_rows, instance="test"),
            mode="upsert",
            dry_run=False,
        )
        rebuilt["subscription_plans"].import_(
            build_envelope("subscription_plans", plan_rows, instance="test"),
            mode="upsert",
            dry_run=False,
        )
        rebuilt["subscription_addons"].import_(
            build_envelope("subscription_addons", addon_rows, instance="test"),
            mode="upsert",
            dry_run=False,
        )

        plan_back = (
            db.session.query(TarifPlan).filter(TarifPlan.slug == plan_slug).first()
        )
        addon_back = db.session.query(AddOn).filter(AddOn.slug == addon_slug).first()
        assert plan_back is not None
        assert plan_back.trial_days == 14
        assert float(plan_back.price) == 29.0
        assert plan_back.billing_period == BillingPeriod.MONTHLY
        assert [c.slug for c in plan_back.categories] == [category_slug]
        assert addon_back is not None
        assert [p.slug for p in addon_back.tarif_plans] == [plan_slug]

    def test_reimport_is_idempotent_upsert(self, db):
        category_slug = _unique_slug("cat")
        db.session.add(TarifPlanCategory(name="Cat", slug=category_slug))
        db.session.commit()

        exchanger = _exchangers(db.session)["subscription_categories"]
        rows = exchanger.export(
            ExportSelector(ids=[category_slug]), include_pii=False
        ).rows
        payload = build_envelope("subscription_categories", rows, instance="test")
        result = exchanger.import_(payload, mode="upsert", dry_run=False)
        assert result.created == 0
        assert result.updated == 1
        assert (
            db.session.query(TarifPlanCategory)
            .filter(TarifPlanCategory.slug == category_slug)
            .count()
            == 1
        )


class TestDryRun:
    def test_dry_run_writes_nothing(self, db):
        plan_slug = _unique_slug("plan")
        rows = [
            {
                "slug": plan_slug,
                "name": "Gold",
                "price": 19.0,
                "billing_period": BillingPeriod.MONTHLY.value,
                "trial_days": 0,
                "is_active": True,
                "sort_order": 0,
                "category_slugs": [],
            }
        ]
        plans = _exchangers(db.session)["subscription_plans"]
        payload = build_envelope("subscription_plans", rows, instance="test")
        result = plans.import_(payload, mode="upsert", dry_run=True)
        assert result.created == 1
        assert (
            db.session.query(TarifPlan).filter(TarifPlan.slug == plan_slug).first()
            is None
        )


class TestRegistrationAndPerms:
    def test_categories_registered_in_sales_cluster(self, db):
        exchangers = _exchangers(db.session)
        assert "subscription_categories" in exchangers
        categories = exchangers["subscription_categories"]
        assert categories.cluster == CLUSTER_SALES
        assert categories.export_permission == PERM_PLANS_VIEW
        assert categories.import_permission == PERM_PLANS_MANAGE

    def test_plans_and_addons_keep_keys_and_perms(self, db):
        exchangers = _exchangers(db.session)
        plans = exchangers["subscription_plans"]
        addons = exchangers["subscription_addons"]
        assert plans.export_permission == PERM_PLANS_VIEW
        assert plans.import_permission == PERM_PLANS_MANAGE
        assert addons.import_permission == PERM_ADDONS_MANAGE

    def test_on_enable_registers_categories(self, db):
        from vbwd.services.data_exchange.registry import data_exchange_registry
        from plugins.subscription import SubscriptionPlugin

        plugin = SubscriptionPlugin()
        plugin.initialize({})
        plugin._register_data_exchangers()

        by_key = {
            exchanger.entity_key: exchanger
            for exchanger in data_exchange_registry.all()
        }
        assert "subscription_categories" in by_key
        assert by_key["subscription_categories"].cluster == CLUSTER_SALES
