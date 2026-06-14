"""Subscription demo / test data — contributed to core via the demo-data
registry (Sprint 03/S5b).

Relocated verbatim from core `vbwd/cli/_demo_seeder.py` (plans/addons) and
`vbwd/testing/test_data_seeder.py` (test plan + subscription). Behaviour is
unchanged (E2); core no longer imports subscription models.
"""
import os
from datetime import datetime, timedelta, timezone

from plugins.subscription.subscription.cache_keys import (
    invalidate_addon_cache,
    invalidate_plan_cache,
)
from vbwd.services.demo_tax_linker import link_demo_tax

TEST_DATA_MARKER = "TEST_DATA_"
TEST_PLAN_SLUG = "test-data-basic-plan"

DEMO_PLANS = [
    {
        "name": "Basic",
        "slug": "basic",
        "description": "Essential features for individuals and small teams.",
        "price_float": 9.99,
        "price": 9.99,
        "currency": "EUR",
        "billing_period": "MONTHLY",
        "is_active": True,
        "sort_order": 1,
        "features": {"api_calls": 1000, "storage_gb": 5, "users": 1},
    },
    {
        "name": "Pro",
        "slug": "pro",
        "description": "Advanced features for growing businesses.",
        "price_float": 29.99,
        "price": 29.99,
        "currency": "EUR",
        "billing_period": "MONTHLY",
        "is_active": True,
        "sort_order": 2,
        "features": {"api_calls": 10000, "storage_gb": 50, "users": 10},
    },
    {
        "name": "Enterprise",
        "slug": "enterprise",
        "description": "Full platform access with premium support.",
        "price_float": 99.99,
        "price": 99.99,
        "currency": "EUR",
        "billing_period": "MONTHLY",
        "is_active": True,
        "sort_order": 3,
        "features": {"api_calls": -1, "storage_gb": 500, "users": -1},
    },
]

# Plans that get a paired, plan-linked user access level seeded alongside the
# catalog. Sourced from DEMO_PLANS by slug (DRY — no second copy of the
# strings); the link is a soft `linked_plan_slug`, resolved on
# `subscription.activated` by `access_level_handler` via
# `find_by_linked_plan_slug`.
USER_ACCESS_LEVEL_PLAN_SLUGS = ["basic", "pro"]

# Public pricing page (/tarifs) hosts the NativePricingPlans widget configured
# with category="root", which calls GET /tarif-plans?category=root. That route
# returns the plans linked to the tarif category with this slug. We seed it and
# link exactly this seeder's demo plans (sourced from DEMO_PLANS — DRY, no
# second copy of the slugs); GHRM packages are seeded by the ghrm plugin and
# stay out of the subscription pricing page.
ROOT_CATEGORY_SLUG = "root"
ROOT_CATEGORY_NAME = "Plans"
ROOT_CATEGORY_PLAN_SLUGS = [plan["slug"] for plan in DEMO_PLANS]

DEMO_ADDONS = [
    {
        "name": "Priority Support",
        "slug": "priority-support",
        "description": "24/7 priority email and chat support with 1-hour response time.",
        "price": 15.00,
        "currency": "EUR",
        "billing_period": "MONTHLY",
        "is_active": True,
        "sort_order": 1,
        "config": {"response_time_hours": 1, "channels": ["email", "chat"]},
    },
    {
        "name": "Premium Analytics",
        "slug": "premium-analytics",
        "description": "Advanced analytics dashboard with custom reports and data export.",
        "price": 25.00,
        "currency": "EUR",
        "billing_period": "MONTHLY",
        "is_active": True,
        "sort_order": 2,
        "config": {"custom_reports": True, "data_export": True, "retention_days": 365},
    },
]


def seed_catalog(session) -> None:
    """Upsert demo plans + addons (core demo seeder delegates here).

    Idempotent by slug: a re-run (or a run where another plugin's seeder, e.g.
    ghrm, already created a plan with the same slug) updates in place instead of
    inserting a duplicate. This is the demo-data registry contract — every hook
    must be a safe idempotent upsert regardless of order (S88).
    """
    from plugins.subscription.subscription.models import TarifPlan, AddOn
    from vbwd.models.enums import BillingPeriod

    plans_by_slug = {}
    for plan_data in DEMO_PLANS:
        plan = session.query(TarifPlan).filter_by(slug=plan_data["slug"]).first()
        if plan is None:
            plan = TarifPlan(slug=plan_data["slug"])
            session.add(plan)
        plan.name = plan_data["name"]
        plan.description = plan_data["description"]
        plan.price = plan_data["price"]
        plan.billing_period = BillingPeriod(plan_data["billing_period"])
        plan.is_active = plan_data["is_active"]
        plan.sort_order = plan_data["sort_order"]
        plan.features = plan_data["features"]
        plans_by_slug[plan_data["slug"]] = plan

    addons = []
    for addon_data in DEMO_ADDONS:
        addon = session.query(AddOn).filter_by(slug=addon_data["slug"]).first()
        if addon is None:
            addon = AddOn(slug=addon_data["slug"])
            session.add(addon)
        addon.name = addon_data["name"]
        addon.description = addon_data["description"]
        addon.price = addon_data["price"]
        addon.billing_period = addon_data["billing_period"]
        addon.is_active = addon_data["is_active"]
        addon.sort_order = addon_data["sort_order"]
        addon.config = addon_data["config"]
        addons.append(addon)

    session.flush()
    seed_root_category(session, plans_by_slug)
    seed_user_access_levels(session)

    # Link the canonical demo VAT to every plan + addon (S85.4) so the price
    # disclosure shows gross > net. Idempotent (re-run does not double-link);
    # the tax is resolved by code through the core linker, no cross-plugin
    # import. No-op when the canonical VAT is absent (taxes not seeded).
    link_demo_tax(session, list(plans_by_slug.values()))
    link_demo_tax(session, addons)

    # A fresh reset-demo must be immediately consistent: clear the TTL-cached
    # public catalog so /tarif-plans* and /addons/ serve the reseeded rows
    # right away instead of a stale body (degrades to a no-op without Redis).
    invalidate_plan_cache()
    invalidate_addon_cache()


def seed_root_category(session, plans_by_slug) -> None:
    """Idempotently upsert the ``root`` tarif category and link the demo plans.

    The public pricing page (/tarifs) lists plans via
    ``GET /tarif-plans?category=root``. Linking is idempotent: a plan already
    in the category is not added a second time. Only this seeder's demo plans
    are linked — GHRM packages stay out of the subscription pricing page.
    """
    from plugins.subscription.subscription.models import TarifPlanCategory

    category = (
        session.query(TarifPlanCategory).filter_by(slug=ROOT_CATEGORY_SLUG).first()
    )
    if category is None:
        category = TarifPlanCategory(slug=ROOT_CATEGORY_SLUG)
        session.add(category)
    category.name = ROOT_CATEGORY_NAME

    linked_ids = {plan.id for plan in category.tarif_plans}
    for plan_slug in ROOT_CATEGORY_PLAN_SLUGS:
        plan = plans_by_slug.get(plan_slug)
        if plan is not None and plan.id not in linked_ids:
            category.tarif_plans.append(plan)

    session.flush()


def seed_user_access_levels(session) -> int:
    """Idempotently create the plan-linked user access levels (basic + pro).

    One ``vbwd_user_access_level`` row is created per slug in
    ``USER_ACCESS_LEVEL_PLAN_SLUGS`` (sourced from ``DEMO_PLANS``). Existence
    is resolved through the core ``UserAccessLevelService`` (no raw SQL); rows
    are created via the ``UserAccessLevel`` ORM model exactly like core's own
    access routes. Re-running is a no-op.

    Returns:
        Number of access levels created on this run.
    """
    from uuid import uuid4
    from vbwd.models.user_access_level import UserAccessLevel
    from vbwd.services.user_access_level_service import UserAccessLevelService

    plans_by_slug = {plan["slug"]: plan for plan in DEMO_PLANS}
    service = UserAccessLevelService(session)
    created_levels = 0
    for plan_slug in USER_ACCESS_LEVEL_PLAN_SLUGS:
        if service.find_by_linked_plan_slug(plan_slug):
            continue
        plan = plans_by_slug[plan_slug]
        session.add(
            UserAccessLevel(
                id=uuid4(),
                name=plan["name"],
                slug=plan_slug,
                description=f"Access level for the {plan['name']} plan.",
                is_system=False,
                linked_plan_slug=plan_slug,
            )
        )
        created_levels += 1

    return created_levels


def seed_test_data(session, test_user) -> None:
    """Create the test tariff plan + an active subscription for the test
    user (core test-data seeder delegates here)."""
    from plugins.subscription.subscription.models import TarifPlan, Subscription
    from vbwd.models.enums import SubscriptionStatus, BillingPeriod

    plan = session.query(TarifPlan).filter_by(slug=TEST_PLAN_SLUG).first()
    if not plan:
        plan = TarifPlan(
            name=f"{TEST_DATA_MARKER}Basic Plan",
            slug=TEST_PLAN_SLUG,
            description="Test plan for integration tests",
            price=9.99,
            is_active=True,
            billing_period=BillingPeriod.MONTHLY,
            features={"api_calls": 1000, "storage_gb": 5},
            sort_order=999,
        )
        session.add(plan)
        session.flush()

    existing = session.query(Subscription).filter_by(user_id=test_user.id).first()
    if existing:
        return

    session.add(
        Subscription(
            user_id=test_user.id,
            tarif_plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            started_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    session.flush()


def clean_test_data(session) -> None:
    """Delete the test users' subscriptions + the test plan (core
    test-data cleaner delegates here, before core deletes the users)."""
    from plugins.subscription.subscription.models import TarifPlan, Subscription
    from vbwd.models.user import User

    test_emails = [
        os.getenv("TEST_USER_EMAIL", "test@example.com"),
        os.getenv("TEST_ADMIN_EMAIL", "admin@example.com"),
    ]
    users = session.query(User).filter(User.email.in_(test_emails)).all()
    for user in users:
        session.query(Subscription).filter_by(user_id=user.id).delete(
            synchronize_session=False
        )

    session.query(TarifPlan).filter(TarifPlan.slug == TEST_PLAN_SLUG).delete(
        synchronize_session=False
    )
