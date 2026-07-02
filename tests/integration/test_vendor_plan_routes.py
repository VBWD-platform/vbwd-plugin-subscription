"""Vendor self-service plan route — gated, permission-checked.

When ``marketplace_enabled`` is False the vendor surface is invisible (403);
when True a user holding ``marketplace.vendor`` can create a plan they own
(``vendor_id`` = their user id).
"""
from uuid import uuid4

import pytest

from plugins.subscription.subscription.routes import vendor_plans


VENDOR_PLANS_PATH = "/api/v1/subscription/vendor/plans"


@pytest.fixture
def client(app):
    return app.test_client()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(app, email):
    from vbwd.extensions import db
    from vbwd.repositories.user_repository import UserRepository

    user_repository = UserRepository(db.session)
    auth_service = app.container.auth_service()
    if user_repository.find_by_email(email) is None:
        auth_service.register(email=email, password="Vendor123@")
        db.session.commit()
    user = user_repository.find_by_email(email)
    login = auth_service.login(email=email, password="Vendor123@")
    return user, login.token


def _grant_vendor_permission(db, user):
    """Attach a user access level carrying ``marketplace.vendor`` to ``user``."""
    from vbwd.models.role import Permission
    from vbwd.models.user_access_level import UserAccessLevel

    permission = (
        db.session.query(Permission).filter_by(name="marketplace.vendor").first()
    )
    if permission is None:
        permission = Permission(
            id=uuid4(),
            name="marketplace.vendor",
            description="Sell as a vendor",
            resource="marketplace",
            action="vendor",
        )
        db.session.add(permission)
    suffix = uuid4().hex[:8]
    level = UserAccessLevel(
        id=uuid4(),
        slug=f"vendor-{suffix}",
        name=f"Vendor {suffix}",
    )
    level.permissions.append(permission)
    user.assigned_user_access_levels.append(level)
    db.session.commit()


def _make_vendor(app, db, email):
    user, token = _register(app, email)
    _grant_vendor_permission(db, user)
    return user, token


def _enable_marketplace(monkeypatch, enabled):
    monkeypatch.setattr(vendor_plans, "marketplace_enabled", lambda: enabled)


def _plan_body(name="Vendor Plan"):
    return {
        "name": name,
        "slug": f"vp-{uuid4().hex[:8]}",
        "price": 12.5,
        "billing_period": "MONTHLY",
    }


def test_vendor_create_blocked_when_marketplace_disabled(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"v-off-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, False)

    resp = client.post(VENDOR_PLANS_PATH, json=_plan_body(), headers=_auth(token))
    assert resp.status_code == 403, resp.get_json()


def test_vendor_create_requires_permission(app, db, client, monkeypatch):
    # A plain user (no marketplace.vendor) is rejected even when enabled.
    _user, token = _register(app, f"plain-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    resp = client.post(VENDOR_PLANS_PATH, json=_plan_body(), headers=_auth(token))
    assert resp.status_code == 403, resp.get_json()


def test_vendor_create_sets_vendor_id(app, db, client, monkeypatch):
    user, token = _make_vendor(app, db, f"v-create-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    resp = client.post(
        VENDOR_PLANS_PATH, json=_plan_body("My Plan"), headers=_auth(token)
    )
    assert resp.status_code == 201, resp.get_json()
    plan = resp.get_json()["plan"]
    assert plan["vendor_id"] == str(user.id)
    assert plan["is_active"] is True


def test_vendor_create_missing_price_is_400(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"v-nop-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    body = _plan_body()
    del body["price"]
    resp = client.post(VENDOR_PLANS_PATH, json=body, headers=_auth(token))
    assert resp.status_code == 400, resp.get_json()


def _create_plan(client, token, name="Vendor Plan"):
    resp = client.post(VENDOR_PLANS_PATH, json=_plan_body(name), headers=_auth(token))
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["plan"]["id"]


# ---- LIST -----------------------------------------------------------------


def test_vendor_list_returns_only_own_plans(app, db, client, monkeypatch):
    _enable_marketplace(monkeypatch, True)
    owner, owner_token = _make_vendor(app, db, f"v-list-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(
        app, db, f"v-lother-{uuid4().hex[:6]}@example.com"
    )

    own_id = _create_plan(client, owner_token, "Mine")
    _create_plan(client, other_token, "Theirs")

    resp = client.get(VENDOR_PLANS_PATH, headers=_auth(owner_token))
    assert resp.status_code == 200, resp.get_json()
    plans = resp.get_json()["plans"]
    ids = {plan["id"] for plan in plans}
    assert own_id in ids
    assert all(plan["vendor_id"] == str(owner.id) for plan in plans)


def test_vendor_list_blocked_when_marketplace_disabled(app, db, client, monkeypatch):
    _owner, token = _make_vendor(app, db, f"v-loff-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, False)

    resp = client.get(VENDOR_PLANS_PATH, headers=_auth(token))
    assert resp.status_code == 403, resp.get_json()


# ---- GET single -----------------------------------------------------------


def test_vendor_get_own_plan(app, db, client, monkeypatch):
    _enable_marketplace(monkeypatch, True)
    _owner, token = _make_vendor(app, db, f"v-get-{uuid4().hex[:6]}@example.com")
    plan_id = _create_plan(client, token, "Getme")

    resp = client.get(f"{VENDOR_PLANS_PATH}/{plan_id}", headers=_auth(token))
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["plan"]["id"] == plan_id


def test_vendor_get_missing_plan_is_404(app, db, client, monkeypatch):
    _enable_marketplace(monkeypatch, True)
    _owner, token = _make_vendor(app, db, f"v-g404-{uuid4().hex[:6]}@example.com")

    resp = client.get(f"{VENDOR_PLANS_PATH}/{uuid4()}", headers=_auth(token))
    assert resp.status_code == 404, resp.get_json()


def test_vendor_get_other_plan_is_403(app, db, client, monkeypatch):
    _enable_marketplace(monkeypatch, True)
    _owner, owner_token = _make_vendor(app, db, f"v-gown-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"v-gno-{uuid4().hex[:6]}@example.com")
    plan_id = _create_plan(client, owner_token)

    resp = client.get(f"{VENDOR_PLANS_PATH}/{plan_id}", headers=_auth(other_token))
    assert resp.status_code == 403, resp.get_json()


# ---- UPDATE ---------------------------------------------------------------


def test_vendor_update_own_plan(app, db, client, monkeypatch):
    _enable_marketplace(monkeypatch, True)
    _owner, token = _make_vendor(app, db, f"v-upd-{uuid4().hex[:6]}@example.com")
    plan_id = _create_plan(client, token, "Old")

    resp = client.put(
        f"{VENDOR_PLANS_PATH}/{plan_id}",
        json={"name": "New Name", "price": 99.0, "is_active": False},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.get_json()
    plan = resp.get_json()["plan"]
    assert plan["name"] == "New Name"
    assert plan["price"] == 99.0
    assert plan["is_active"] is False


def test_vendor_update_invalid_billing_period_is_400(app, db, client, monkeypatch):
    _enable_marketplace(monkeypatch, True)
    _owner, token = _make_vendor(app, db, f"v-updb-{uuid4().hex[:6]}@example.com")
    plan_id = _create_plan(client, token)

    resp = client.put(
        f"{VENDOR_PLANS_PATH}/{plan_id}",
        json={"billing_period": "WEEKLY"},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.get_json()


def test_vendor_update_invalid_price_is_400(app, db, client, monkeypatch):
    _enable_marketplace(monkeypatch, True)
    _owner, token = _make_vendor(app, db, f"v-updp-{uuid4().hex[:6]}@example.com")
    plan_id = _create_plan(client, token)

    resp = client.put(
        f"{VENDOR_PLANS_PATH}/{plan_id}",
        json={"price": "not-a-number"},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.get_json()


def test_vendor_update_other_plan_is_403(app, db, client, monkeypatch):
    _enable_marketplace(monkeypatch, True)
    _owner, owner_token = _make_vendor(app, db, f"v-uown-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"v-uno-{uuid4().hex[:6]}@example.com")
    plan_id = _create_plan(client, owner_token)

    resp = client.put(
        f"{VENDOR_PLANS_PATH}/{plan_id}",
        json={"name": "hack"},
        headers=_auth(other_token),
    )
    assert resp.status_code == 403, resp.get_json()


def test_vendor_update_missing_plan_is_404(app, db, client, monkeypatch):
    _enable_marketplace(monkeypatch, True)
    _owner, token = _make_vendor(app, db, f"v-u404-{uuid4().hex[:6]}@example.com")

    resp = client.put(
        f"{VENDOR_PLANS_PATH}/{uuid4()}", json={"name": "x"}, headers=_auth(token)
    )
    assert resp.status_code == 404, resp.get_json()


# ---- DELETE ---------------------------------------------------------------


def test_vendor_delete_own_plan(app, db, client, monkeypatch):
    _enable_marketplace(monkeypatch, True)
    _owner, token = _make_vendor(app, db, f"v-del-{uuid4().hex[:6]}@example.com")
    plan_id = _create_plan(client, token)

    resp = client.delete(f"{VENDOR_PLANS_PATH}/{plan_id}", headers=_auth(token))
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["success"] is True

    follow = client.get(f"{VENDOR_PLANS_PATH}/{plan_id}", headers=_auth(token))
    assert follow.status_code == 404, follow.get_json()


def test_vendor_delete_other_plan_is_403(app, db, client, monkeypatch):
    _enable_marketplace(monkeypatch, True)
    _owner, owner_token = _make_vendor(app, db, f"v-down-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"v-dno-{uuid4().hex[:6]}@example.com")
    plan_id = _create_plan(client, owner_token)

    resp = client.delete(f"{VENDOR_PLANS_PATH}/{plan_id}", headers=_auth(other_token))
    assert resp.status_code == 403, resp.get_json()


def test_vendor_delete_missing_plan_is_404(app, db, client, monkeypatch):
    _enable_marketplace(monkeypatch, True)
    _owner, token = _make_vendor(app, db, f"v-d404-{uuid4().hex[:6]}@example.com")

    resp = client.delete(f"{VENDOR_PLANS_PATH}/{uuid4()}", headers=_auth(token))
    assert resp.status_code == 404, resp.get_json()
