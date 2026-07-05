"""Integration tests for plan-Features driven user access levels.

A plan whose ``features`` carries ``access_levels: premium, vip`` grants the
named core ``AccessLevel`` rows on activation and revokes them on cancellation,
overlap-safe against the user's other still-active plans. Exercised through both
the dedicated ``PlanFeatureAccessLevelService`` and the wired
``SubscriptionAccessLevelHandler`` (the full event-path, proving the restructure
runs the Features grant regardless of a ``linked_plan_slug`` level).
"""
from uuid import uuid4

from vbwd.models.enums import BillingPeriod, SubscriptionStatus
from vbwd.models.user import User
from vbwd.models.user_access_level import AccessLevel
from vbwd.services.user_access_level_service import UserAccessLevelService

from plugins.subscription.subscription.models import Subscription, TarifPlan
from plugins.subscription.subscription.handlers.access_level_handler import (
    SubscriptionAccessLevelHandler,
)
from plugins.subscription.subscription.services.plan_feature_access_level_service import (  # noqa: E501
    PlanFeatureAccessLevelService,
)


def _user(db) -> User:
    user = User(email=f"pf-access-{uuid4().hex}@example.com", password_hash="x")
    db.session.add(user)
    db.session.flush()
    return user


def _access_level(db, slug: str) -> AccessLevel:
    level = AccessLevel(id=uuid4(), name=f"Level {slug}", slug=slug)
    db.session.add(level)
    db.session.flush()
    return level


def _plan(db, features: dict) -> TarifPlan:
    plan = TarifPlan(
        id=uuid4(),
        name=f"plan-{uuid4().hex}",
        slug=f"plan-{uuid4().hex}",
        description="plan",
        price=9.99,
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
        sort_order=0,
        features=features,
    )
    db.session.add(plan)
    db.session.flush()
    return plan


def _subscribe(db, user_id, plan_id, status=SubscriptionStatus.ACTIVE):
    subscription = Subscription(user_id=user_id, tarif_plan_id=plan_id, status=status)
    db.session.add(subscription)
    db.session.flush()
    return subscription


def _user_level_slugs(db, user_id):
    refreshed = db.session.get(User, user_id)
    return {level.slug for level in refreshed.assigned_user_access_levels}


class TestGrantForPlan:
    def test_grants_all_declared_access_levels(self, db):
        _access_level(db, "premium")
        _access_level(db, "vip")
        user = _user(db)
        plan = _plan(db, {"access_levels": "premium, vip"})
        db.session.commit()

        PlanFeatureAccessLevelService().grant_for_plan(user.id, plan.id)
        db.session.commit()

        assert _user_level_slugs(db, user.id) == {"premium", "vip"}

    def test_unknown_slug_is_skipped_not_fatal(self, db):
        _access_level(db, "premium")
        user = _user(db)
        plan = _plan(db, {"access_levels": "premium, ghost"})
        db.session.commit()

        # Must not raise; the known slug is still granted.
        PlanFeatureAccessLevelService().grant_for_plan(user.id, plan.id)
        db.session.commit()

        assert _user_level_slugs(db, user.id) == {"premium"}

    def test_plan_without_access_levels_grants_nothing(self, db):
        _access_level(db, "premium")
        user = _user(db)
        plan = _plan(db, {"other": "marketing bullet"})
        db.session.commit()

        PlanFeatureAccessLevelService().grant_for_plan(user.id, plan.id)
        db.session.commit()

        assert _user_level_slugs(db, user.id) == set()


class TestRevokeForCancelledPlan:
    def test_overlap_retains_shared_level_revokes_exclusive(self, db):
        premium = _access_level(db, "premium")
        vip = _access_level(db, "vip")
        user = _user(db)
        plan_a = _plan(db, {"access_levels": "premium, vip"})
        plan_b = _plan(db, {"access_levels": "vip"})
        # Both active; user already holds both levels.
        _subscribe(db, user.id, plan_a.id)
        _subscribe(db, user.id, plan_b.id)
        service = UserAccessLevelService(db.session)
        service.assign(user.id, premium.id)
        service.assign(user.id, vip.id)
        db.session.commit()

        # Cancel plan A — 'vip' still declared by active plan B, 'premium' is not.
        PlanFeatureAccessLevelService().revoke_for_cancelled_plan(user.id, plan_a.id)
        db.session.commit()

        assert _user_level_slugs(db, user.id) == {"vip"}

    def test_revokes_all_when_no_other_active_plan(self, db):
        premium = _access_level(db, "premium")
        vip = _access_level(db, "vip")
        user = _user(db)
        plan_a = _plan(db, {"access_levels": "premium, vip"})
        _subscribe(db, user.id, plan_a.id, status=SubscriptionStatus.CANCELLED)
        service = UserAccessLevelService(db.session)
        service.assign(user.id, premium.id)
        service.assign(user.id, vip.id)
        db.session.commit()

        PlanFeatureAccessLevelService().revoke_for_cancelled_plan(user.id, plan_a.id)
        db.session.commit()

        assert _user_level_slugs(db, user.id) == set()


class TestHandlerEventPath:
    def test_activated_grants_features_levels_without_linked_level(self, db):
        """The restructured handler runs the Features grant even when the plan
        has no ``linked_plan_slug`` access level (the old early-return case)."""
        _access_level(db, "premium")
        _access_level(db, "vip")
        user = _user(db)
        plan = _plan(db, {"access_levels": "premium, vip"})
        db.session.commit()

        SubscriptionAccessLevelHandler().on_subscription_activated(
            "subscription.activated",
            {
                "user_id": str(user.id),
                "plan_id": str(plan.id),
                "plan_slug": plan.slug,
            },
        )

        assert _user_level_slugs(db, user.id) == {"premium", "vip"}

    def test_cancelled_revokes_features_levels_overlap_safe(self, db):
        premium = _access_level(db, "premium")
        vip = _access_level(db, "vip")
        user = _user(db)
        plan_a = _plan(db, {"access_levels": "premium, vip"})
        plan_b = _plan(db, {"access_levels": "vip"})
        _subscribe(db, user.id, plan_a.id)
        _subscribe(db, user.id, plan_b.id)
        service = UserAccessLevelService(db.session)
        service.assign(user.id, premium.id)
        service.assign(user.id, vip.id)
        db.session.commit()

        SubscriptionAccessLevelHandler().on_subscription_cancelled(
            "subscription.cancelled",
            {
                "user_id": str(user.id),
                "plan_id": str(plan_a.id),
                "plan_slug": plan_a.slug,
            },
        )

        assert _user_level_slugs(db, user.id) == {"vip"}
