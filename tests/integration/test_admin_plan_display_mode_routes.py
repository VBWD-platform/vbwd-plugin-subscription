"""S72.4 — admin create/update plan accept ``price_display_mode`` (integration).

Contract:
- POST/PUT accept ``price_display_mode`` of ``null`` / ``"netto"`` / ``"brutto"``.
- An unknown value is rejected with 400.
- The persisted plan's ``to_dict()`` reflects the stored override.
"""
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from vbwd.models.enums import BillingPeriod, UserRole, UserStatus
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


def _make_plan(db):
    from plugins.subscription.subscription.models.tarif_plan import TarifPlan

    plan = TarifPlan(
        id=uuid4(),
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        price=Decimal("100.00"),
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


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


HEADERS = {"Authorization": "Bearer valid"}


def test_create_plan_with_display_mode_override(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/tarif-plans/",
        json={
            "name": "NettoPlan",
            "price": "100.00",
            "billing_period": "MONTHLY",
            "price_display_mode": "netto",
        },
        headers=HEADERS,
    )

    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["plan"]["price_display_mode"] == "netto"


def test_create_plan_default_display_mode_is_null(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/tarif-plans/",
        json={
            "name": "InheritPlan",
            "price": "100.00",
            "billing_period": "MONTHLY",
        },
        headers=HEADERS,
    )

    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["plan"]["price_display_mode"] is None


def test_create_plan_rejects_unknown_display_mode(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/tarif-plans/",
        json={
            "name": "BadMode",
            "price": "100.00",
            "billing_period": "MONTHLY",
            "price_display_mode": "gross",
        },
        headers=HEADERS,
    )

    assert resp.status_code == 400, resp.get_json()


def test_update_plan_sets_display_mode_override(db, client, monkeypatch):
    admin = _make_admin(db)
    plan = _make_plan(db)
    _auth_as_admin(monkeypatch, admin)

    resp = client.put(
        f"/api/v1/admin/tarif-plans/{plan.id}",
        json={"price_display_mode": "brutto"},
        headers=HEADERS,
    )

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["plan"]["price_display_mode"] == "brutto"


def test_update_plan_clears_display_mode_to_inherit(db, client, monkeypatch):
    admin = _make_admin(db)
    plan = _make_plan(db)
    plan.price_display_mode = "netto"
    db.session.commit()
    _auth_as_admin(monkeypatch, admin)

    resp = client.put(
        f"/api/v1/admin/tarif-plans/{plan.id}",
        json={"price_display_mode": None},
        headers=HEADERS,
    )

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["plan"]["price_display_mode"] is None


def test_update_plan_rejects_unknown_display_mode(db, client, monkeypatch):
    admin = _make_admin(db)
    plan = _make_plan(db)
    _auth_as_admin(monkeypatch, admin)

    resp = client.put(
        f"/api/v1/admin/tarif-plans/{plan.id}",
        json={"price_display_mode": "weird"},
        headers=HEADERS,
    )

    assert resp.status_code == 400, resp.get_json()
