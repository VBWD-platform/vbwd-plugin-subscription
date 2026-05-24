"""Subscription demo / test data — contributed to core via the demo-data
registry (Sprint 03/S5b).

Relocated verbatim from core `vbwd/cli/_demo_seeder.py` (plans/addons) and
`vbwd/testing/test_data_seeder.py` (test plan + subscription). Behaviour is
unchanged (E2); core no longer imports subscription models.
"""
import os
from datetime import datetime, timedelta, timezone

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
    """Insert demo plans + addons (core demo seeder delegates here)."""
    from plugins.subscription.subscription.models import TarifPlan, AddOn
    from vbwd.models.enums import BillingPeriod

    for plan_data in DEMO_PLANS:
        session.add(
            TarifPlan(
                name=plan_data["name"],
                slug=plan_data["slug"],
                description=plan_data["description"],
                price_float=plan_data["price_float"],
                price=plan_data["price"],
                currency=plan_data["currency"],
                billing_period=BillingPeriod(plan_data["billing_period"]),
                is_active=plan_data["is_active"],
                sort_order=plan_data["sort_order"],
                features=plan_data["features"],
            )
        )

    for addon_data in DEMO_ADDONS:
        session.add(
            AddOn(
                name=addon_data["name"],
                slug=addon_data["slug"],
                description=addon_data["description"],
                price=addon_data["price"],
                currency=addon_data["currency"],
                billing_period=addon_data["billing_period"],
                is_active=addon_data["is_active"],
                sort_order=addon_data["sort_order"],
                config=addon_data["config"],
            )
        )


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
            price_float=9.99,
            price=9.99,
            currency="EUR",
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
