"""Integration tests for ``PermissionSyncService.reconcile_user`` (S69).

Exercises the full consumer path against a real DB and the core
``DefaultUserPermissionGrant`` port: plan grant, overlap-safe revoke, add-on
grant/revoke, special-permission gating, and idempotency.
"""
from uuid import uuid4

import pytest

from vbwd.models.enums import BillingPeriod, SubscriptionStatus
from vbwd.models.role import Permission
from vbwd.models.user import User

from plugins.subscription.subscription.models import (
    AddOn,
    AddOnSubscription,
    Subscription,
    TarifPlan,
)
from plugins.subscription.subscription.services.permission_sync_service import (
    PermissionSyncService,
)


def _permission(db, name: str) -> Permission:
    existing = db.session.query(Permission).filter_by(name=name).first()
    if existing:
        return existing
    parts = name.rsplit(".", 1)
    permission = Permission(
        id=uuid4(),
        name=name,
        resource=parts[0] if len(parts) > 1 else name,
        action=parts[1] if len(parts) > 1 else "*",
        description=name,
    )
    db.session.add(permission)
    db.session.flush()
    return permission


def _user(db) -> User:
    user = User(email=f"s69-sync-{uuid4().hex}@example.com", password_hash="x")
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
    return PermissionSyncService()


def test_plan_grants_declared_permission(db, service):
    _permission(db, "analytics.view")
    user = _user(db)
    plan = _plan(db, f"pro-{uuid4().hex}", {"permissions_enable": ["analytics.view"]})
    _subscribe(db, user.id, plan.id)
    db.session.commit()

    service.reconcile_user(user.id)
    db.session.commit()

    refreshed = db.session.get(User, user.id)
    assert refreshed.has_user_permission("analytics.view") is True
    level_slugs = {level.slug for level in refreshed.assigned_user_access_levels}
    assert f"auto-plan-{plan.slug}" in level_slugs


def test_overlap_keeps_permission_until_last_source_cancelled(db, service):
    _permission(db, "analytics.view")
    user = _user(db)
    plan_a = _plan(db, f"a-{uuid4().hex}", {"permissions_enable": ["analytics.view"]})
    plan_b = _plan(db, f"b-{uuid4().hex}", {"permissions_enable": ["analytics.view"]})
    sub_a = _subscribe(db, user.id, plan_a.id)
    _subscribe(db, user.id, plan_b.id)
    db.session.commit()

    service.reconcile_user(user.id)
    db.session.commit()
    assert db.session.get(User, user.id).has_user_permission("analytics.view")

    # Cancel plan A — permission still backed by plan B.
    sub_a.status = SubscriptionStatus.CANCELLED
    db.session.commit()
    service.reconcile_user(user.id)
    db.session.commit()
    assert db.session.get(User, user.id).has_user_permission("analytics.view")

    # Cancel plan B — now nothing grants it.
    for subscription in db.session.query(Subscription).filter_by(user_id=user.id):
        subscription.status = SubscriptionStatus.CANCELLED
    db.session.commit()
    service.reconcile_user(user.id)
    db.session.commit()
    assert not db.session.get(User, user.id).has_user_permission("analytics.view")


def test_addon_grants_and_revokes_permission(db, service):
    _permission(db, "shop.products.view")
    user = _user(db)
    addon = _addon(
        db, f"extra-{uuid4().hex}", {"permissions_enable": ["shop.products.view"]}
    )
    addon_subscription = _subscribe_addon(db, user.id, addon.id)
    db.session.commit()

    service.reconcile_user(user.id)
    db.session.commit()
    assert db.session.get(User, user.id).has_user_permission("shop.products.view")

    addon_subscription.status = SubscriptionStatus.CANCELLED
    db.session.commit()
    service.reconcile_user(user.id)
    db.session.commit()
    assert not db.session.get(User, user.id).has_user_permission("shop.products.view")


def test_special_permission_ignored_when_flag_off(db, service, app):
    _permission(db, "reports.admin")
    user = _user(db)
    plan = _plan(
        db,
        f"vip-{uuid4().hex}",
        {"special_permissions_enable": ["reports.admin"]},
    )
    _subscribe(db, user.id, plan.id)
    db.session.commit()

    app.config["allow_plan_special_permissions"] = False
    service.reconcile_user(user.id)
    db.session.commit()

    # No managed role created.
    from vbwd.models.role import Role

    role = db.session.query(Role).filter_by(slug=f"auto-plan-{plan.slug}").first()
    assert role is None


def test_special_permission_granted_via_role_when_flag_on(db, service, app):
    _permission(db, "reports.admin")
    user = _user(db)
    plan = _plan(
        db,
        f"vip-{uuid4().hex}",
        {"special_permissions_enable": ["reports.admin", "*", "settings.system"]},
    )
    _subscribe(db, user.id, plan.id)
    db.session.commit()

    app.config["allow_plan_special_permissions"] = True
    try:
        service.reconcile_user(user.id)
        db.session.commit()

        from vbwd.models.role import Role

        role = db.session.query(Role).filter_by(slug=f"auto-plan-{plan.slug}").first()
        assert role is not None
        granted = {permission.name for permission in role.permissions}
        assert "reports.admin" in granted
        # D4: wildcard + deny-listed perms are clamped out.
        assert "*" not in granted
        assert "settings.system" not in granted
    finally:
        app.config["allow_plan_special_permissions"] = False


def test_reconcile_is_idempotent(db, service):
    _permission(db, "analytics.view")
    user = _user(db)
    plan = _plan(db, f"idem-{uuid4().hex}", {"permissions_enable": ["analytics.view"]})
    _subscribe(db, user.id, plan.id)
    db.session.commit()

    service.reconcile_user(user.id)
    db.session.commit()
    service.reconcile_user(user.id)
    db.session.commit()

    refreshed = db.session.get(User, user.id)
    matching = [
        level
        for level in refreshed.assigned_user_access_levels
        if level.slug == f"auto-plan-{plan.slug}"
    ]
    assert len(matching) == 1


def test_unknown_permission_is_skipped_not_fatal(db, service):
    user = _user(db)
    plan = _plan(
        db,
        f"unknown-{uuid4().hex}",
        {"permissions_enable": ["does.not.exist"]},
    )
    _subscribe(db, user.id, plan.id)
    db.session.commit()

    # Must not raise.
    service.reconcile_user(user.id)
    db.session.commit()

    refreshed = db.session.get(User, user.id)
    assert refreshed.has_user_permission("does.not.exist") is False
