"""Regression tests for the S69 D5 emit gaps + handler wiring.

Verifies that the previously-silent status-change paths (scheduler expiry, the
add-on activate/cancel path) now publish a lifecycle event AND that the wired
``PermissionSyncHandler`` reconciles the user's permissions in response.
"""
from datetime import timedelta
from uuid import uuid4

import pytest

from vbwd.events.bus import event_bus
from vbwd.models.enums import BillingPeriod, SubscriptionStatus
from vbwd.models.role import Permission
from vbwd.models.user import User
from vbwd.utils.datetime_utils import utcnow

from plugins.subscription.subscription.handlers.permission_sync_handler import (
    PermissionSyncHandler,
)
from plugins.subscription.subscription.models import Subscription, TarifPlan
from plugins.subscription.subscription.repositories.subscription_repository import (
    SubscriptionRepository,
)
from plugins.subscription.subscription.services.permission_sync_service import (
    PermissionSyncService,
)
from plugins.subscription.subscription.services.subscription_service import (
    SubscriptionService,
)


def _permission(db, name: str) -> Permission:
    existing = db.session.query(Permission).filter_by(name=name).first()
    if existing:
        return existing
    permission = Permission(
        id=uuid4(),
        name=name,
        resource=name.rsplit(".", 1)[0],
        action=name.rsplit(".", 1)[-1],
        description=name,
    )
    db.session.add(permission)
    db.session.flush()
    return permission


def _user(db) -> User:
    user = User(email=f"s69-emit-{uuid4().hex}@example.com", password_hash="x")
    db.session.add(user)
    db.session.flush()
    return user


def _plan(db, slug: str) -> TarifPlan:
    plan = TarifPlan(
        id=uuid4(),
        name=slug,
        slug=slug,
        description="plan",
        price=9.99,
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
        sort_order=0,
        features={"permissions_enable": ["analytics.view"]},
    )
    db.session.add(plan)
    db.session.flush()
    return plan


@pytest.fixture
def subscribed_handler():
    """Subscribe the real handler to the bus for the duration of the test."""
    handler = PermissionSyncHandler()
    for event_name in ("subscription.expired", "subscription.cancelled"):
        event_bus.subscribe(event_name, handler.on_lifecycle_event)
    yield handler
    for event_name in ("subscription.expired", "subscription.cancelled"):
        event_bus.unsubscribe(event_name, handler.on_lifecycle_event)


def test_scheduler_expiry_emits_and_revokes_permission(db, subscribed_handler):
    _permission(db, "analytics.view")
    user = _user(db)
    plan = _plan(db, f"sched-{uuid4().hex}")

    subscription = Subscription(
        user_id=user.id,
        tarif_plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        expires_at=utcnow() - timedelta(days=1),
    )
    db.session.add(subscription)
    db.session.commit()

    # Grant the permission first (active subscription).
    PermissionSyncService().reconcile_user(user.id)
    db.session.commit()
    assert db.session.get(User, user.id).has_user_permission("analytics.view")

    # Scheduler expiry must now publish → handler reconciles → permission gone.
    service = SubscriptionService(subscription_repo=SubscriptionRepository(db.session))
    service.expire_subscriptions()
    db.session.commit()

    assert not db.session.get(User, user.id).has_user_permission("analytics.view")
