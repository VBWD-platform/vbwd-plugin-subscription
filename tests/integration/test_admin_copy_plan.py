"""Integration: POST /api/v1/admin/tarif-plans/<plan_id>/copy.

Oracle for the copy-plan naming contract: copying a plan named "Pro" must
produce a new plan named "Pro (Copy)" (capital C), with a distinct slug, while
the source plan is left unchanged.
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


def _make_plan(db, *, name):
    from plugins.subscription.subscription.models.tarif_plan import TarifPlan

    plan = TarifPlan(
        id=uuid4(),
        name=name,
        slug=f"{name.lower()}-{uuid4().hex[:8]}",
        price=Decimal("100.00"),
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def _auth_as_admin(monkeypatch, admin):
    """Patch require_auth + RBAC so g.user is `admin` with the manage permission."""
    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = admin
    svc = MagicMock()
    svc.verify_token.return_value = str(admin.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: svc)
    monkeypatch.setattr(type(admin), "is_admin", property(lambda self: True))
    monkeypatch.setattr(type(admin), "has_permission", lambda self, perm: True)


def test_copy_plan_appends_capital_copy_suffix(db, client, monkeypatch):
    admin = _make_admin(db)
    source_plan = _make_plan(db, name="Pro")
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        f"/api/v1/admin/tarif-plans/{source_plan.id}/copy",
        headers={"Authorization": "Bearer valid"},
    )

    assert resp.status_code == 201, resp.get_json()
    copied = resp.get_json()["plan"]
    assert copied["name"] == "Pro (Copy)"
    assert copied["slug"] != source_plan.slug

    from plugins.subscription.subscription.models.tarif_plan import TarifPlan

    refreshed_source = db.session.get(TarifPlan, source_plan.id)
    assert refreshed_source.name == "Pro"
