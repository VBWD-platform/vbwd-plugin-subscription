"""Integration tests (real PostgreSQL) for the S49.0 entitlement read.

Seeds active, trialing, cancelled and expired subscriptions for a user and
asserts:
  - ``SubscriptionRepository.find_active_by_user_list`` returns only the
    ACTIVE + TRIALING rows.
  - ``SubscriptionReadModel.active_plan_ids`` returns the distinct plan ids of
    those active subscriptions (cancelled/expired excluded).
"""
from uuid import uuid4

from vbwd.models.enums import BillingPeriod, SubscriptionStatus
from vbwd.models.user import User

from plugins.subscription.subscription.models import Subscription, TarifPlan
from plugins.subscription.subscription.repositories.subscription_repository import (
    SubscriptionRepository,
)
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)
from plugins.subscription.subscription.services.subscription_read_model import (
    SubscriptionReadModel,
)


def _user(db) -> User:
    user = User(email=f"s49-{uuid4().hex}@example.com", password_hash="x")
    db.session.add(user)
    db.session.flush()
    return user


def _plan(db, slug: str) -> TarifPlan:
    plan = TarifPlan(
        id=uuid4(),
        name=slug,
        slug=slug,
        description="plan",
        price_float=9.99,
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
        sort_order=0,
    )
    return TarifPlanRepository(db.session).save(plan)


def _subscription(db, user_id, plan_id, status: SubscriptionStatus) -> Subscription:
    subscription = Subscription(
        user_id=user_id,
        tarif_plan_id=plan_id,
        status=status,
    )
    db.session.add(subscription)
    db.session.flush()
    return subscription


def test_find_active_by_user_list_filters_active_and_trialing_only(db):
    user = _user(db)
    plan_active = _plan(db, f"active-{uuid4().hex}")
    plan_trialing = _plan(db, f"trialing-{uuid4().hex}")
    plan_cancelled = _plan(db, f"cancelled-{uuid4().hex}")
    plan_expired = _plan(db, f"expired-{uuid4().hex}")

    _subscription(db, user.id, plan_active.id, SubscriptionStatus.ACTIVE)
    _subscription(db, user.id, plan_trialing.id, SubscriptionStatus.TRIALING)
    _subscription(db, user.id, plan_cancelled.id, SubscriptionStatus.CANCELLED)
    _subscription(db, user.id, plan_expired.id, SubscriptionStatus.EXPIRED)
    db.session.commit()

    active_subscriptions = SubscriptionRepository(db.session).find_active_by_user_list(
        user.id
    )

    returned_statuses = {subscription.status for subscription in active_subscriptions}
    assert returned_statuses == {
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.TRIALING,
    }


def test_active_plan_ids_returns_only_active_plan_ids(db, monkeypatch):
    user = _user(db)
    plan_active = _plan(db, f"active-{uuid4().hex}")
    plan_trialing = _plan(db, f"trialing-{uuid4().hex}")
    plan_expired = _plan(db, f"expired-{uuid4().hex}")

    _subscription(db, user.id, plan_active.id, SubscriptionStatus.ACTIVE)
    _subscription(db, user.id, plan_trialing.id, SubscriptionStatus.TRIALING)
    _subscription(db, user.id, plan_expired.id, SubscriptionStatus.EXPIRED)
    db.session.commit()

    read_model = SubscriptionReadModel()
    monkeypatch.setattr(
        read_model,
        "_subscription_repo",
        lambda: SubscriptionRepository(db.session),
    )

    plan_ids = read_model.active_plan_ids(user.id)

    assert set(plan_ids) == {plan_active.id, plan_trialing.id}
    assert plan_expired.id not in plan_ids
