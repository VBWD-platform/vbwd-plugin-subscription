"""S154 D4 — the admin/vendor plan routes trigger the IndexNow ping.

End-to-end through the real Flask route (not just the ``seo_indexnow`` unit),
so the wiring from the plan routes into ``notify_plan_changed`` /
``notify_plan_renamed`` is exercised. The cms import is monkeypatched at
``seo_indexnow._import_ping_indexnow_urls`` (no real cms/network needed), and
the fe-admin "indexable" switch + ``plans_slug`` are driven through the real
``config_store``.

Engineering requirements (binding, restated): TDD-first · DevOps-first · SOLID ·
DI · DRY · Liskov · clean code · NO OVERENGINEERING.
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


def _make_plan(db, **overrides):
    from plugins.subscription.subscription.models.tarif_plan import TarifPlan

    fields = {
        "id": uuid4(),
        "name": "Pro",
        "slug": f"pro-{uuid4().hex[:8]}",
        "price": Decimal("100.00"),
        "billing_period": BillingPeriod.MONTHLY,
        "is_active": True,
    }
    fields.update(overrides)
    plan = TarifPlan(**fields)
    db.session.add(plan)
    db.session.commit()
    return plan


class _IndexableConfigStore:
    """A fake ``config_store`` driving the plans "indexable" switch + slug."""

    def __init__(self, plans_indexable, plans_slug="plans"):
        self._config = {
            "seo_index_tarif_plans": plans_indexable,
            "plans_slug": plans_slug,
        }

    def get_config(self, plugin_name):
        return self._config if plugin_name == "subscription" else {}


HEADERS = {"Authorization": "Bearer valid"}


def _fake_ping(monkeypatch):
    from plugins.subscription.subscription.services import seo_indexnow

    ping = MagicMock()
    monkeypatch.setattr(seo_indexnow, "_import_ping_indexnow_urls", lambda: ping)
    return ping


def test_create_plan_pings_indexnow_when_switch_on(app, db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    ping = _fake_ping(monkeypatch)
    monkeypatch.setattr(app, "config_store", _IndexableConfigStore(True))

    resp = client.post(
        "/api/v1/admin/tarif-plans/",
        json={
            "name": "Gold",
            "slug": "gold-plan",
            "price": "50.00",
            "billing_period": "MONTHLY",
        },
        headers=HEADERS,
    )

    assert resp.status_code == 201, resp.get_json()
    ping.assert_called_once_with(["/plans/gold-plan", "/plans"])


def test_create_plan_does_not_ping_when_switch_off(app, db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    ping = _fake_ping(monkeypatch)
    monkeypatch.setattr(app, "config_store", _IndexableConfigStore(False))

    resp = client.post(
        "/api/v1/admin/tarif-plans/",
        json={
            "name": "Silver",
            "slug": "silver-plan",
            "price": "30.00",
            "billing_period": "MONTHLY",
        },
        headers=HEADERS,
    )

    assert resp.status_code == 201, resp.get_json()
    ping.assert_not_called()


def test_update_plan_pings_indexnow(app, db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    plan = _make_plan(db, slug="update-plan")
    ping = _fake_ping(monkeypatch)
    monkeypatch.setattr(app, "config_store", _IndexableConfigStore(True))

    resp = client.put(
        f"/api/v1/admin/tarif-plans/{plan.id}",
        json={"price": "75.00"},
        headers=HEADERS,
    )

    assert resp.status_code == 200, resp.get_json()
    ping.assert_called_once_with(["/plans/update-plan", "/plans"])


def test_delete_plan_pings_indexnow_with_its_slug(app, db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    plan = _make_plan(db, slug="delete-plan")
    ping = _fake_ping(monkeypatch)
    monkeypatch.setattr(app, "config_store", _IndexableConfigStore(True))

    resp = client.delete(
        f"/api/v1/admin/tarif-plans/{plan.id}",
        headers=HEADERS,
    )

    assert resp.status_code == 200, resp.get_json()
    ping.assert_called_once_with(["/plans/delete-plan", "/plans"])


def test_deactivate_plan_pings_indexnow(app, db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    plan = _make_plan(db, slug="deactivate-plan")
    ping = _fake_ping(monkeypatch)
    monkeypatch.setattr(app, "config_store", _IndexableConfigStore(True))

    resp = client.post(
        f"/api/v1/admin/tarif-plans/{plan.id}/deactivate",
        headers=HEADERS,
    )

    assert resp.status_code == 200, resp.get_json()
    ping.assert_called_once_with(["/plans/deactivate-plan", "/plans"])


def test_activate_plan_respects_the_configured_plans_slug(app, db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    plan = _make_plan(db, slug="activate-plan", is_active=False)
    ping = _fake_ping(monkeypatch)
    monkeypatch.setattr(
        app, "config_store", _IndexableConfigStore(True, plans_slug="pricing")
    )

    resp = client.post(
        f"/api/v1/admin/tarif-plans/{plan.id}/activate",
        headers=HEADERS,
    )

    assert resp.status_code == 200, resp.get_json()
    ping.assert_called_once_with(["/pricing/activate-plan", "/pricing"])
