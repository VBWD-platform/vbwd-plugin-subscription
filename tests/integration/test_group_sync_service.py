"""Integration tests for ``GroupSyncService.reconcile_user_groups`` (S73).

Exercises the full consumer path against a real DB and the core
``DefaultUserGroupMembership`` port: plan check-in adds, plan check-out removes,
check-out wins across active sources, last-check-in-source cancellation removes
the managed membership, un-managed memberships are never touched, add-on
check-in/out, and idempotency.

The test seeds ``UserGroup`` rows directly (a TEST may import core models); the
plugin SOURCE reaches groups only through the core port (oracle-enforced).
"""
from uuid import uuid4

import pytest

from vbwd.models.enums import BillingPeriod, SubscriptionStatus
from vbwd.models.user import User
from vbwd.models.user_group import UserGroup
from vbwd.services.user_group_membership import DefaultUserGroupMembership

from plugins.subscription.subscription.models import (
    AddOn,
    AddOnSubscription,
    Subscription,
    TarifPlan,
)
from plugins.subscription.subscription.services.group_sync_service import (
    GroupSyncService,
)


def _group(db, slug: str) -> UserGroup:
    group = UserGroup(id=uuid4(), slug=slug, name=slug.title())
    db.session.add(group)
    db.session.flush()
    return group


def _user(db) -> User:
    user = User(email=f"s73-grp-{uuid4().hex}@example.com", password_hash="x")
    db.session.add(user)
    db.session.flush()
    return user


def _plan(db, slug: str, features: dict) -> TarifPlan:
    plan = TarifPlan(
        id=uuid4(),
        name=slug,
        slug=slug,
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


def _addon(db, slug: str, config: dict) -> AddOn:
    addon = AddOn(
        id=uuid4(),
        name=slug,
        slug=slug,
        price=1,
        billing_period=BillingPeriod.MONTHLY.value,
        config=config,
    )
    db.session.add(addon)
    db.session.flush()
    return addon


def _subscribe(db, user_id, plan_id, status=SubscriptionStatus.ACTIVE):
    subscription = Subscription(user_id=user_id, tarif_plan_id=plan_id, status=status)
    db.session.add(subscription)
    db.session.flush()
    return subscription


def _subscribe_addon(db, user_id, addon_id, status=SubscriptionStatus.ACTIVE):
    addon_subscription = AddOnSubscription(
        user_id=user_id, addon_id=addon_id, status=status
    )
    db.session.add(addon_subscription)
    db.session.flush()
    return addon_subscription


@pytest.fixture
def service(db):
    return GroupSyncService()


def _slugs(db, user_id):
    return DefaultUserGroupMembership(db.session).list_user_group_slugs(user_id)


def test_plan_checkin_adds_to_group(db, service):
    _group(db, "vip")
    user = _user(db)
    plan = _plan(db, f"pro-{uuid4().hex}", {"user_checkin_group": "vip"})
    _subscribe(db, user.id, plan.id)
    db.session.commit()

    service.reconcile_user_groups(user.id)
    db.session.commit()

    assert "vip" in _slugs(db, user.id)


def test_plan_checkout_removes_from_group(db, service):
    _group(db, "trial")
    user = _user(db)
    # Pre-existing managed membership the checkout source should remove.
    DefaultUserGroupMembership(db.session).add(user.id, "trial")
    plan = _plan(db, f"paid-{uuid4().hex}", {"user_checkout_group": "trial"})
    _subscribe(db, user.id, plan.id)
    db.session.commit()

    service.reconcile_user_groups(user.id)
    db.session.commit()

    assert "trial" not in _slugs(db, user.id)


def test_checkout_wins_across_active_sources(db, service):
    _group(db, "promo")
    user = _user(db)
    plan_in = _plan(db, f"in-{uuid4().hex}", {"user_checkin_group": "promo"})
    plan_out = _plan(db, f"out-{uuid4().hex}", {"user_checkout_group": "promo"})
    _subscribe(db, user.id, plan_in.id)
    _subscribe(db, user.id, plan_out.id)
    db.session.commit()

    service.reconcile_user_groups(user.id)
    db.session.commit()

    assert "promo" not in _slugs(db, user.id)


def test_cancelling_only_checkin_source_removes_managed_membership(db, service):
    _group(db, "vip")
    user = _user(db)
    plan = _plan(db, f"pro-{uuid4().hex}", {"user_checkin_group": "vip"})
    subscription = _subscribe(db, user.id, plan.id)
    db.session.commit()

    service.reconcile_user_groups(user.id)
    db.session.commit()
    assert "vip" in _slugs(db, user.id)

    subscription.status = SubscriptionStatus.CANCELLED
    db.session.commit()
    service.reconcile_user_groups(user.id)
    db.session.commit()

    assert "vip" not in _slugs(db, user.id)


def test_unmanaged_membership_is_never_touched(db, service):
    _group(db, "vip")
    _group(db, "manual")
    user = _user(db)
    # 'manual' is admin-controlled — no source mentions it.
    DefaultUserGroupMembership(db.session).add(user.id, "manual")
    plan = _plan(db, f"pro-{uuid4().hex}", {"user_checkin_group": "vip"})
    _subscribe(db, user.id, plan.id)
    db.session.commit()

    service.reconcile_user_groups(user.id)
    db.session.commit()

    slugs = _slugs(db, user.id)
    assert "vip" in slugs
    assert "manual" in slugs


def test_addon_checkin_and_checkout(db, service):
    _group(db, "addon-vip")
    user = _user(db)
    addon = _addon(db, f"extra-{uuid4().hex}", {"user_checkin_group": "addon-vip"})
    addon_subscription = _subscribe_addon(db, user.id, addon.id)
    db.session.commit()

    service.reconcile_user_groups(user.id)
    db.session.commit()
    assert "addon-vip" in _slugs(db, user.id)

    addon_subscription.status = SubscriptionStatus.CANCELLED
    db.session.commit()
    service.reconcile_user_groups(user.id)
    db.session.commit()
    assert "addon-vip" not in _slugs(db, user.id)


def test_reconcile_is_idempotent(db, service):
    _group(db, "vip")
    user = _user(db)
    plan = _plan(db, f"idem-{uuid4().hex}", {"user_checkin_group": "vip"})
    _subscribe(db, user.id, plan.id)
    db.session.commit()

    service.reconcile_user_groups(user.id)
    db.session.commit()
    service.reconcile_user_groups(user.id)
    db.session.commit()

    assert _slugs(db, user.id) == {"vip"}


def test_list_form_checkin_group_supported(db, service):
    _group(db, "a")
    _group(db, "b")
    user = _user(db)
    plan = _plan(db, f"multi-{uuid4().hex}", {"user_checkin_group": ["a", "b"]})
    _subscribe(db, user.id, plan.id)
    db.session.commit()

    service.reconcile_user_groups(user.id)
    db.session.commit()

    assert _slugs(db, user.id) == {"a", "b"}
