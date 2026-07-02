"""Vendor self-service tarif-plan tags / custom-field routes.

Mirrors ``test_vendor_plan_routes.py`` and the shop plugin's
``test_vendor_stock_media_meta_routes.py``: every route is gated behind
``marketplace_enabled`` AND the ``marketplace.vendor`` permission, and every
plan-scoped route enforces ``vendor_id == g.user_id`` ownership (a foreign
vendor gets 403). The underlying primitive is the SAME the admin routes use
(the core ``tags_and_custom_fields()`` port keyed on the ``tarif_plan`` entity
type) — vendor + admin must never diverge. Plans have no stock and no images,
so only tags + custom-fields are exposed.
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


def _create_plan(client, token, name="Vendor Plan"):
    resp = client.post(VENDOR_PLANS_PATH, json=_plan_body(name), headers=_auth(token))
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["plan"]["id"]


def _create_field_def(db, key="tier", field_type="text"):
    from vbwd.models.custom_field_def import CustomFieldDef

    definition = CustomFieldDef(
        id=uuid4(),
        entity_type="tarif_plan",
        key=key,
        label=key.title(),
        type=field_type,
        is_active=True,
    )
    db.session.add(definition)
    db.session.commit()
    return definition


# ── Tags ─────────────────────────────────────────────────────────────


def test_tags_get_empty_by_default(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"pt-g-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    plan_id = _create_plan(client, token)
    resp = client.get(f"{VENDOR_PLANS_PATH}/{plan_id}/tags", headers=_auth(token))
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["tags"] == []


def test_tags_get_blocked_when_disabled(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"pt-d-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    plan_id = _create_plan(client, token)

    _enable_marketplace(monkeypatch, False)
    resp = client.get(f"{VENDOR_PLANS_PATH}/{plan_id}/tags", headers=_auth(token))
    assert resp.status_code == 403, resp.get_json()


def test_tags_get_requires_permission(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"pt-po-{uuid4().hex[:6]}@example.com")
    _plain, plain_token = _register(app, f"plain-t-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    plan_id = _create_plan(client, owner_token)
    resp = client.get(f"{VENDOR_PLANS_PATH}/{plan_id}/tags", headers=_auth(plain_token))
    assert resp.status_code == 403, resp.get_json()


def test_tags_put_replaces(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"pt-p-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    plan_id = _create_plan(client, token)
    resp = client.put(
        f"{VENDOR_PLANS_PATH}/{plan_id}/tags",
        json={"tags": ["featured", "new"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.get_json()
    assert set(resp.get_json()["tags"]) == {"featured", "new"}

    read = client.get(f"{VENDOR_PLANS_PATH}/{plan_id}/tags", headers=_auth(token))
    assert set(read.get_json()["tags"]) == {"featured", "new"}


def test_tags_put_other_vendor_403(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"pt-o-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"pt-x-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    plan_id = _create_plan(client, owner_token)
    resp = client.put(
        f"{VENDOR_PLANS_PATH}/{plan_id}/tags",
        json={"tags": ["hack"]},
        headers=_auth(other_token),
    )
    assert resp.status_code == 403, resp.get_json()


def test_tags_get_missing_plan_404(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"pt-404-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    resp = client.get(f"{VENDOR_PLANS_PATH}/{uuid4()}/tags", headers=_auth(token))
    assert resp.status_code == 404, resp.get_json()


def test_tags_put_requires_list(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"pt-r-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    plan_id = _create_plan(client, token)
    resp = client.put(
        f"{VENDOR_PLANS_PATH}/{plan_id}/tags",
        json={"tags": "not-a-list"},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.get_json()


# ── Custom fields ────────────────────────────────────────────────────


def test_custom_fields_get_empty_by_default(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"pc-g-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    plan_id = _create_plan(client, token)
    resp = client.get(
        f"{VENDOR_PLANS_PATH}/{plan_id}/custom-fields", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["custom_fields"] == {}


def test_custom_fields_put_upserts(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"pc-p-{uuid4().hex[:6]}@example.com")
    field_key = f"tier_{uuid4().hex[:6]}"
    _create_field_def(db, key=field_key)
    _enable_marketplace(monkeypatch, True)

    plan_id = _create_plan(client, token)
    resp = client.put(
        f"{VENDOR_PLANS_PATH}/{plan_id}/custom-fields",
        json={"custom_fields": {field_key: "gold"}},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["custom_fields"][field_key] == "gold"

    read = client.get(
        f"{VENDOR_PLANS_PATH}/{plan_id}/custom-fields", headers=_auth(token)
    )
    assert read.get_json()["custom_fields"][field_key] == "gold"


def test_custom_fields_put_unknown_key_400(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"pc-u-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    plan_id = _create_plan(client, token)
    resp = client.put(
        f"{VENDOR_PLANS_PATH}/{plan_id}/custom-fields",
        json={"custom_fields": {"nope_unknown_key": "x"}},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.get_json()


def test_custom_fields_put_other_vendor_403(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"pc-o-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"pc-x-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    plan_id = _create_plan(client, owner_token)
    resp = client.put(
        f"{VENDOR_PLANS_PATH}/{plan_id}/custom-fields",
        json={"custom_fields": {"any": "x"}},
        headers=_auth(other_token),
    )
    assert resp.status_code == 403, resp.get_json()


def test_custom_fields_put_requires_object(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"pc-r-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    plan_id = _create_plan(client, token)
    resp = client.put(
        f"{VENDOR_PLANS_PATH}/{plan_id}/custom-fields",
        json={"custom_fields": ["not", "an", "object"]},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.get_json()


def test_custom_fields_get_missing_plan_404(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"pc-404-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    resp = client.get(
        f"{VENDOR_PLANS_PATH}/{uuid4()}/custom-fields", headers=_auth(token)
    )
    assert resp.status_code == 404, resp.get_json()
