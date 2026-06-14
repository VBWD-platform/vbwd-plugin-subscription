"""Integration test for ``SubscriptionReadModel.active_addon_ids`` (S69).

Mirrors ``active_plan_ids``: returns the distinct add-on ids the user is
actively entitled to (ACTIVE or TRIALING add-on subscriptions only).
"""
from uuid import uuid4

from vbwd.models.enums import BillingPeriod, SubscriptionStatus
from vbwd.models.user import User

from plugins.subscription.subscription.models import AddOn, AddOnSubscription
from plugins.subscription.subscription.repositories.addon_subscription_repository import (  # noqa: E501
    AddOnSubscriptionRepository,
)
from plugins.subscription.subscription.services.subscription_read_model import (
    SubscriptionReadModel,
)


def _user(db) -> User:
    user = User(email=f"s69-addon-{uuid4().hex}@example.com", password_hash="x")
    db.session.add(user)
    db.session.flush()
    return user


def _addon(db, slug: str) -> AddOn:
    addon = AddOn(
        id=uuid4(),
        name=slug,
        slug=slug,
        price=1,
        billing_period=BillingPeriod.MONTHLY.value,
        config={},
    )
    db.session.add(addon)
    db.session.flush()
    return addon


def _addon_subscription(db, user_id, addon_id, status) -> AddOnSubscription:
    addon_subscription = AddOnSubscription(
        user_id=user_id,
        addon_id=addon_id,
        status=status,
    )
    db.session.add(addon_subscription)
    db.session.flush()
    return addon_subscription


def test_active_addon_ids_returns_active_and_trialing_only(db, monkeypatch):
    user = _user(db)
    addon_active = _addon(db, f"active-{uuid4().hex}")
    addon_trialing = _addon(db, f"trialing-{uuid4().hex}")
    addon_cancelled = _addon(db, f"cancelled-{uuid4().hex}")

    _addon_subscription(db, user.id, addon_active.id, SubscriptionStatus.ACTIVE)
    _addon_subscription(db, user.id, addon_trialing.id, SubscriptionStatus.TRIALING)
    _addon_subscription(db, user.id, addon_cancelled.id, SubscriptionStatus.CANCELLED)
    db.session.commit()

    read_model = SubscriptionReadModel()
    monkeypatch.setattr(
        read_model,
        "_addon_subscription_repo",
        lambda: AddOnSubscriptionRepository(db.session),
    )

    addon_ids = read_model.active_addon_ids(user.id)

    assert set(addon_ids) == {addon_active.id, addon_trialing.id}
    assert addon_cancelled.id not in addon_ids
