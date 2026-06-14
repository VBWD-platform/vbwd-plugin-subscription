"""S72.3 — admin create/update plan accept ``tax_ids`` (integration, real PG).

Contract:
- POST/PUT accept ``tax_ids: [uuid]``; each must exist AND be active.
- Update is a replace-set; duplicate ids are deduped.
- A nonexistent or inactive tax id is rejected with 400.
- The persisted plan's ``to_dict()`` reflects the assigned taxes.
"""
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from vbwd.models.enums import BillingPeriod, UserRole, UserStatus
from vbwd.models.user import User
from vbwd.models.tax import Tax


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


def _make_tax(db, *, is_active=True, rate="19.00"):
    tax = Tax(
        id=uuid4(),
        name=f"Tax {uuid4().hex[:6]}",
        code=f"TX_{uuid4().hex[:6]}",
        rate=Decimal(rate),
        is_active=is_active,
    )
    db.session.add(tax)
    db.session.commit()
    return tax


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


def test_create_plan_with_tax_ids_persists_m2m(db, client, monkeypatch):
    admin = _make_admin(db)
    tax_one = _make_tax(db)
    tax_two = _make_tax(db)
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/tarif-plans/",
        json={
            "name": "Taxed",
            "price": "100.00",
            "billing_period": "MONTHLY",
            "tax_ids": [str(tax_one.id), str(tax_two.id), str(tax_one.id)],
        },
        headers=HEADERS,
    )

    assert resp.status_code == 201, resp.get_json()
    plan = resp.get_json()["plan"]
    # Deduped.
    assert sorted(plan["tax_ids"]) == sorted([str(tax_one.id), str(tax_two.id)])


def test_create_plan_rejects_inactive_tax(db, client, monkeypatch):
    admin = _make_admin(db)
    inactive = _make_tax(db, is_active=False)
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/tarif-plans/",
        json={
            "name": "BadTax",
            "price": "10.00",
            "billing_period": "MONTHLY",
            "tax_ids": [str(inactive.id)],
        },
        headers=HEADERS,
    )

    assert resp.status_code == 400, resp.get_json()


def test_create_plan_rejects_unknown_tax(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/tarif-plans/",
        json={
            "name": "GhostTax",
            "price": "10.00",
            "billing_period": "MONTHLY",
            "tax_ids": [str(uuid4())],
        },
        headers=HEADERS,
    )

    assert resp.status_code == 400, resp.get_json()


def test_update_plan_replace_set_of_tax_ids(db, client, monkeypatch):
    admin = _make_admin(db)
    plan = _make_plan(db)
    first = _make_tax(db)
    second = _make_tax(db)
    plan.taxes = [first]
    db.session.commit()
    _auth_as_admin(monkeypatch, admin)

    resp = client.put(
        f"/api/v1/admin/tarif-plans/{plan.id}",
        json={"tax_ids": [str(second.id)]},
        headers=HEADERS,
    )

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["plan"]["tax_ids"] == [str(second.id)]


def test_update_plan_empty_tax_ids_clears_assignment(db, client, monkeypatch):
    admin = _make_admin(db)
    plan = _make_plan(db)
    tax = _make_tax(db)
    plan.taxes = [tax]
    db.session.commit()
    _auth_as_admin(monkeypatch, admin)

    resp = client.put(
        f"/api/v1/admin/tarif-plans/{plan.id}",
        json={"tax_ids": []},
        headers=HEADERS,
    )

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["plan"]["tax_ids"] == []
