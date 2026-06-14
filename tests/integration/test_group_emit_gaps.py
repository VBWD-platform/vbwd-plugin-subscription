"""Regression test for the S73 group-sync handler wiring (mirrors S69 D5).

Verifies that a lifecycle event (scheduler expiry) drives the wired
``GroupSyncHandler`` to reconcile the user's MANAGED group memberships — the
same emit-gap closing S69 added for the permission path.
"""
from datetime import timedelta
from uuid import uuid4

import pytest

from vbwd.events.bus import event_bus
from vbwd.models.enums import BillingPeriod, SubscriptionStatus
from vbwd.models.user import User
from vbwd.models.user_group import UserGroup
from vbwd.services.user_group_membership import DefaultUserGroupMembership
from vbwd.utils.datetime_utils import utcnow

from plugins.subscription.subscription.handlers.group_sync_handler import (
    GroupSyncHandler,
)
from plugins.subscription.subscription.models import Subscription, TarifPlan
from plugins.subscription.subscription.repositories.subscription_repository import (
    SubscriptionRepository,
)
from plugins.subscription.subscription.services.group_sync_service import (
    GroupSyncService,
)
from plugins.subscription.subscription.services.subscription_service import (
    SubscriptionService,
)


@pytest.fixture
def subscribed_handler():
    handler = GroupSyncHandler()
    for event_name in ("subscription.expired", "subscription.cancelled"):
        event_bus.subscribe(event_name, handler.on_lifecycle_event)
    yield handler
    for event_name in ("subscription.expired", "subscription.cancelled"):
        event_bus.unsubscribe(event_name, handler.on_lifecycle_event)


def test_scheduler_expiry_emits_and_removes_managed_group(db, subscribed_handler):
    db.session.add(UserGroup(id=uuid4(), slug="vip", name="VIP"))
    user = User(email=f"s73-emit-{uuid4().hex}@example.com", password_hash="x")
    db.session.add(user)
    db.session.flush()

    plan = TarifPlan(
        id=uuid4(),
        name=f"sched-{uuid4().hex}",
        slug=f"sched-{uuid4().hex}",
        description="plan",
        price=9.99,
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
        sort_order=0,
        features={"user_checkin_group": "vip"},
    )
    db.session.add(plan)
    subscription = Subscription(
        user_id=user.id,
        tarif_plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        expires_at=utcnow() - timedelta(days=1),
    )
    db.session.add(subscription)
    db.session.commit()

    GroupSyncService().reconcile_user_groups(user.id)
    db.session.commit()
    assert "vip" in DefaultUserGroupMembership(db.session).list_user_group_slugs(
        user.id
    )

    service = SubscriptionService(subscription_repo=SubscriptionRepository(db.session))
    service.expire_subscriptions()
    db.session.commit()

    assert "vip" not in DefaultUserGroupMembership(db.session).list_user_group_slugs(
        user.id
    )
