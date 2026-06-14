"""Integration: GET /api/v1/admin/subscription/users/<user_id>/addons.

S50.3 — the add-ons read moved out of core (`/admin/users/<id>/addons`) into
this subscription-plugin endpoint. The response shape (``addon_subscriptions``
list with the same per-item keys) is preserved so the fe-admin user-detail
"Add-Ons" tab needs no change beyond the URL.
"""
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from vbwd.models.enums import (
    SubscriptionStatus,
    UserRole,
    UserStatus,
)
from vbwd.models.user import User


def _make_admin(db):
    admin = User(
        id=uuid4(),
        email=f"admin-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
    )
    db.session.add(admin)
    db.session.commit()
    return admin


def _make_user(db):
    user = User(
        id=uuid4(),
        email=f"user-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _make_addon_subscription(db, user_id):
    from plugins.subscription.subscription.models.addon import AddOn
    from plugins.subscription.subscription.models.addon_subscription import (
        AddOnSubscription,
    )

    addon = AddOn(
        id=uuid4(),
        name="Extra Storage",
        slug=f"extra-storage-{uuid4().hex[:8]}",
        price=Decimal("5.00"),
        billing_period="monthly",
    )
    db.session.add(addon)
    db.session.commit()

    addon_subscription = AddOnSubscription(
        id=uuid4(),
        user_id=user_id,
        addon_id=addon.id,
        status=SubscriptionStatus.ACTIVE,
    )
    db.session.add(addon_subscription)
    db.session.commit()
    return addon_subscription


def _auth_as_admin(monkeypatch, admin):
    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = admin
    svc = MagicMock()
    svc.verify_token.return_value = str(admin.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: svc)
    monkeypatch.setattr(type(admin), "is_admin", property(lambda self: True))
    monkeypatch.setattr(type(admin), "has_permission", lambda self, perm: True)


def test_admin_user_addons_returns_addon_subscriptions(db, client, monkeypatch):
    admin = _make_admin(db)
    user = _make_user(db)
    _make_addon_subscription(db, user.id)
    _auth_as_admin(monkeypatch, admin)

    resp = client.get(
        f"/api/v1/admin/subscription/users/{user.id}/addons",
        headers={"Authorization": "Bearer valid"},
    )

    assert resp.status_code == 200, resp.get_json()
    payload = resp.get_json()
    assert "addon_subscriptions" in payload
    assert len(payload["addon_subscriptions"]) == 1
    item = payload["addon_subscriptions"][0]
    assert item["addon_name"] == "Extra Storage"
    assert item["status"] == "ACTIVE"
    # Shape preserved (the keys the fe-admin tab renders).
    for key in (
        "id",
        "addon_name",
        "status",
        "invoice_status",
        "first_invoice",
        "last_invoice",
    ):
        assert key in item


def test_admin_user_addons_404_for_unknown_user(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    resp = client.get(
        f"/api/v1/admin/subscription/users/{uuid4()}/addons",
        headers={"Authorization": "Bearer valid"},
    )

    assert resp.status_code == 404
