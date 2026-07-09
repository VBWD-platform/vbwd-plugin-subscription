"""Integration: copy (duplicate) for TarifPlan and AddOn — per-item + bulk.

Oracles for the universal copy contract (real PostgreSQL):

- A copy is ALWAYS inactive (``is_active = False``) — copies are never live.
- The copy's slug is unique and collision-safe: ``<base>-copy`` then
  ``<base>-copy-2``, ``-copy-3`` ... even when the SAME source is copied twice
  (per-item or via the bulk endpoint) and even when two plans are bulk-copied
  in a single request.
- M2M links (plan taxes, plan categories, plan<->addon links; addon taxes,
  addon<->plan links) are RE-POINTED at the same rows, never duplicated.
- A copied plan carries ZERO user subscriptions (transactions are not copied).
- Bulk copy skips unknown ids (non-fatal) and returns every created row + a
  count. The bulk endpoints require the sibling per-item permission.
"""
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from vbwd.models.enums import (
    BillingPeriod,
    SubscriptionStatus,
    UserRole,
    UserStatus,
)
from vbwd.models.tax import Tax
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


def _make_plan(db, *, name="Pro"):
    from plugins.subscription.subscription.models.tarif_plan import TarifPlan

    plan = TarifPlan(
        id=uuid4(),
        name=name,
        slug=f"{name.lower()}-{uuid4().hex[:8]}",
        description="original description",
        price=Decimal("100.00"),
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
        sort_order=7,
        price_display_mode="brutto",
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def _make_addon(db, *, name="Extra"):
    from plugins.subscription.subscription.models.addon import AddOn

    addon = AddOn(
        id=uuid4(),
        name=name,
        slug=f"{name.lower()}-{uuid4().hex[:8]}",
        description="addon description",
        price=Decimal("5.00"),
        billing_period=BillingPeriod.MONTHLY.value,
        config={"foo": "bar"},
        is_active=True,
        sort_order=3,
    )
    db.session.add(addon)
    db.session.commit()
    return addon


def _make_tax(db, *, rate="19.00"):
    tax = Tax(
        id=uuid4(),
        name=f"Tax {uuid4().hex[:6]}",
        code=f"TX_{uuid4().hex[:6]}",
        rate=Decimal(rate),
        is_active=True,
    )
    db.session.add(tax)
    db.session.commit()
    return tax


def _make_category(db, *, name="Group"):
    from plugins.subscription.subscription.models.tarif_plan_category import (
        TarifPlanCategory,
    )

    category = TarifPlanCategory(
        id=uuid4(),
        name=name,
        slug=f"{name.lower()}-{uuid4().hex[:8]}",
        is_single=True,
    )
    db.session.add(category)
    db.session.commit()
    return category


def _make_subscription(db, *, user, plan):
    from plugins.subscription.subscription.models.subscription import Subscription

    sub = Subscription(
        id=uuid4(),
        user_id=user.id,
        tarif_plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
    )
    db.session.add(sub)
    db.session.commit()
    return sub


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


AUTH_HEADER = {"Authorization": "Bearer valid"}


# --------------------------------------------------------------------------- #
# Per-item plan copy — the fixed existing endpoint                            #
# --------------------------------------------------------------------------- #
def test_copy_plan_is_always_inactive(db, client, monkeypatch):
    admin = _make_admin(db)
    plan = _make_plan(db, name="Pro")
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(f"/api/v1/admin/tarif-plans/{plan.id}/copy", headers=AUTH_HEADER)

    assert resp.status_code == 201, resp.get_json()
    copied = resp.get_json()["plan"]
    assert copied["is_active"] is False
    assert copied["name"] == "Pro (Copy)"
    assert copied["slug"] == f"{plan.slug}-copy"


def test_copy_plan_copies_extended_scalar_fields(db, client, monkeypatch):
    admin = _make_admin(db)
    plan = _make_plan(db, name="Pro")
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(f"/api/v1/admin/tarif-plans/{plan.id}/copy", headers=AUTH_HEADER)

    copied = resp.get_json()["plan"]
    assert copied["price_display_mode"] == "brutto"
    assert copied["description"] == "original description"


def test_copy_plan_slug_collision_is_safe(db, client, monkeypatch):
    admin = _make_admin(db)
    plan = _make_plan(db, name="Pro")
    _auth_as_admin(monkeypatch, admin)

    first = client.post(
        f"/api/v1/admin/tarif-plans/{plan.id}/copy", headers=AUTH_HEADER
    )
    second = client.post(
        f"/api/v1/admin/tarif-plans/{plan.id}/copy", headers=AUTH_HEADER
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.get_json()["plan"]["slug"] == f"{plan.slug}-copy"
    assert second.get_json()["plan"]["slug"] == f"{plan.slug}-copy-2"


def test_copy_plan_repoints_taxes_categories_addons(db, client, monkeypatch):
    from plugins.subscription.subscription.models.addon import AddOn
    from plugins.subscription.subscription.models.tarif_plan import TarifPlan
    from plugins.subscription.subscription.models.tarif_plan_category import (
        TarifPlanCategory,
    )

    admin = _make_admin(db)
    plan = _make_plan(db, name="Pro")
    tax = _make_tax(db)
    category = _make_category(db)
    addon = _make_addon(db)

    plan.taxes = [tax]
    plan.categories = [category]
    addon.tarif_plans = [plan]
    db.session.commit()

    tax_count_before = db.session.query(Tax).count()
    category_count_before = db.session.query(TarifPlanCategory).count()
    addon_count_before = db.session.query(AddOn).count()

    _auth_as_admin(monkeypatch, admin)
    resp = client.post(f"/api/v1/admin/tarif-plans/{plan.id}/copy", headers=AUTH_HEADER)
    assert resp.status_code == 201, resp.get_json()
    copy_id = resp.get_json()["plan"]["id"]

    # No new tax / category / addon rows were created — links re-point.
    assert db.session.query(Tax).count() == tax_count_before
    assert db.session.query(TarifPlanCategory).count() == category_count_before
    assert db.session.query(AddOn).count() == addon_count_before

    copy = db.session.get(TarifPlan, copy_id)
    assert {str(t.id) for t in copy.taxes} == {str(tax.id)}
    assert {str(c.id) for c in copy.categories} == {str(category.id)}

    # The addon now lists BOTH the source and the copy (re-pointed, not moved).
    db.session.refresh(addon)
    linked_plan_ids = {str(tp.id) for tp in addon.tarif_plans}
    assert linked_plan_ids == {str(plan.id), copy_id}


def test_copy_plan_does_not_copy_subscriptions(db, client, monkeypatch):
    from plugins.subscription.subscription.models.subscription import Subscription
    from plugins.subscription.subscription.models.tarif_plan import TarifPlan

    admin = _make_admin(db)
    plan = _make_plan(db, name="Pro")
    _make_subscription(db, user=admin, plan=plan)
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(f"/api/v1/admin/tarif-plans/{plan.id}/copy", headers=AUTH_HEADER)
    copy_id = resp.get_json()["plan"]["id"]

    copy = db.session.get(TarifPlan, copy_id)
    assert copy.subscriptions.count() == 0
    # Source keeps its subscription untouched.
    assert (
        db.session.query(Subscription)
        .filter(Subscription.tarif_plan_id == plan.id)
        .count()
        == 1
    )


def test_copy_plan_source_missing_returns_404(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(f"/api/v1/admin/tarif-plans/{uuid4()}/copy", headers=AUTH_HEADER)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Bulk plan copy                                                              #
# --------------------------------------------------------------------------- #
def test_bulk_copy_plans_returns_all_created(db, client, monkeypatch):
    admin = _make_admin(db)
    plan_a = _make_plan(db, name="Alpha")
    plan_b = _make_plan(db, name="Beta")
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/tarif-plans/bulk/copy",
        json={"ids": [str(plan_a.id), str(plan_b.id)]},
        headers=AUTH_HEADER,
    )

    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["count"] == 2
    created_slugs = {p["slug"] for p in body["plans"]}
    assert created_slugs == {f"{plan_a.slug}-copy", f"{plan_b.slug}-copy"}
    assert all(p["is_active"] is False for p in body["plans"])


def test_bulk_copy_plans_same_id_twice_is_collision_safe(db, client, monkeypatch):
    admin = _make_admin(db)
    plan = _make_plan(db, name="Pro")
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/tarif-plans/bulk/copy",
        json={"ids": [str(plan.id), str(plan.id)]},
        headers=AUTH_HEADER,
    )

    assert resp.status_code == 201, resp.get_json()
    slugs = {p["slug"] for p in resp.get_json()["plans"]}
    assert slugs == {f"{plan.slug}-copy", f"{plan.slug}-copy-2"}


def test_bulk_copy_plans_skips_unknown_ids(db, client, monkeypatch):
    admin = _make_admin(db)
    plan = _make_plan(db, name="Pro")
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/tarif-plans/bulk/copy",
        json={"ids": [str(plan.id), str(uuid4())]},
        headers=AUTH_HEADER,
    )

    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["count"] == 1
    assert body["plans"][0]["slug"] == f"{plan.slug}-copy"


def test_bulk_copy_plans_requires_permission(db, client, monkeypatch):
    admin = _make_admin(db)
    plan = _make_plan(db, name="Pro")

    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = admin
    svc = MagicMock()
    svc.verify_token.return_value = str(admin.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: svc)
    monkeypatch.setattr(type(admin), "is_admin", property(lambda self: True))
    monkeypatch.setattr(type(admin), "has_permission", lambda self, perm: False)

    resp = client.post(
        "/api/v1/admin/tarif-plans/bulk/copy",
        json={"ids": [str(plan.id)]},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Bulk addon copy                                                            #
# --------------------------------------------------------------------------- #
def test_bulk_copy_addons_returns_all_created_inactive(db, client, monkeypatch):
    admin = _make_admin(db)
    addon_a = _make_addon(db, name="Alpha")
    addon_b = _make_addon(db, name="Beta")
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/addons/bulk/copy",
        json={"ids": [str(addon_a.id), str(addon_b.id)]},
        headers=AUTH_HEADER,
    )

    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["count"] == 2
    created_slugs = {a["slug"] for a in body["addons"]}
    assert created_slugs == {f"{addon_a.slug}-copy", f"{addon_b.slug}-copy"}
    assert all(a["is_active"] is False for a in body["addons"])
    assert all(a["name"].endswith("(Copy)") for a in body["addons"])


def test_bulk_copy_addons_repoints_taxes_and_plans(db, client, monkeypatch):
    from plugins.subscription.subscription.models.addon import AddOn

    admin = _make_admin(db)
    addon = _make_addon(db, name="Extra")
    plan = _make_plan(db, name="Pro")
    tax = _make_tax(db)
    addon.taxes = [tax]
    addon.tarif_plans = [plan]
    db.session.commit()

    tax_count_before = db.session.query(Tax).count()
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/addons/bulk/copy",
        json={"ids": [str(addon.id)]},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 201, resp.get_json()
    copy_id = resp.get_json()["addons"][0]["id"]

    assert db.session.query(Tax).count() == tax_count_before
    copy = db.session.get(AddOn, copy_id)
    assert {str(t.id) for t in copy.taxes} == {str(tax.id)}
    assert {str(tp.id) for tp in copy.tarif_plans} == {str(plan.id)}
    assert copy.config == {"foo": "bar"}


def test_bulk_copy_addons_slug_collision_is_safe(db, client, monkeypatch):
    admin = _make_admin(db)
    addon = _make_addon(db, name="Extra")
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/addons/bulk/copy",
        json={"ids": [str(addon.id), str(addon.id)]},
        headers=AUTH_HEADER,
    )

    assert resp.status_code == 201, resp.get_json()
    slugs = {a["slug"] for a in resp.get_json()["addons"]}
    assert slugs == {f"{addon.slug}-copy", f"{addon.slug}-copy-2"}


def test_bulk_copy_addons_skips_unknown_ids(db, client, monkeypatch):
    admin = _make_admin(db)
    addon = _make_addon(db, name="Extra")
    _auth_as_admin(monkeypatch, admin)

    resp = client.post(
        "/api/v1/admin/addons/bulk/copy",
        json={"ids": [str(uuid4()), str(addon.id)]},
        headers=AUTH_HEADER,
    )

    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["count"] == 1
    assert body["addons"][0]["slug"] == f"{addon.slug}-copy"


def test_bulk_copy_addons_requires_permission(db, client, monkeypatch):
    admin = _make_admin(db)
    addon = _make_addon(db, name="Extra")

    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = admin
    svc = MagicMock()
    svc.verify_token.return_value = str(admin.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: svc)
    monkeypatch.setattr(type(admin), "is_admin", property(lambda self: True))
    monkeypatch.setattr(type(admin), "has_permission", lambda self, perm: False)

    resp = client.post(
        "/api/v1/admin/addons/bulk/copy",
        json={"ids": [str(addon.id)]},
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 403
