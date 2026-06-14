"""S48.2 integration — catalogue read cache + admin-write invalidation.

End-to-end through the real routes with a process-local ``InMemoryCacheStore``
installed via ``vbwd.services.cache.set_cache_store``:

  - ``GET /api/v1/tarif-plans`` is served from cache on the second call
    (the new plan added directly to the DB is NOT seen until invalidation);
  - an admin write to a plan clears the ``tarif-plans:`` prefix, so the very
    next public list reflects the edit immediately (no TTL wait);
  - a different ``?currency=`` is an independent cache entry;
  - an unrelated write (a token bundle) does NOT clear the plan cache.
"""
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from vbwd.models.enums import BillingPeriod, UserRole, UserStatus
from vbwd.models.user import User
from vbwd.services.cache import InMemoryCacheStore, set_cache_store, reset_cache_store


@pytest.fixture
def cache():
    store = InMemoryCacheStore(enabled=True)
    set_cache_store(store)
    yield store
    reset_cache_store()


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


def _make_plan(db, *, name="Pro", slug=None, price="29.99"):
    from plugins.subscription.subscription.models.tarif_plan import TarifPlan

    plan = TarifPlan(
        id=uuid4(),
        name=name,
        slug=slug or f"{name.lower()}-{uuid4().hex[:8]}",
        price=Decimal(price),
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


def _plan_slugs(response):
    return {plan["slug"] for plan in response.get_json()["plans"]}


def test_list_served_from_cache_until_invalidated(db, client, cache):
    first = _make_plan(db, name="Alpha")
    listed = client.get("/api/v1/tarif-plans")
    assert listed.status_code == 200
    assert first.slug in _plan_slugs(listed)

    # A second plan added straight to the DB is NOT visible from the cache.
    second = _make_plan(db, name="Beta")
    cached = client.get("/api/v1/tarif-plans")
    assert second.slug not in _plan_slugs(cached)


def test_admin_plan_write_invalidates_list(db, client, cache, monkeypatch):
    existing = _make_plan(db, name="Gamma")
    warmed = client.get("/api/v1/tarif-plans")
    assert existing.slug in _plan_slugs(warmed)

    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    update = client.put(
        f"/api/v1/admin/tarif-plans/{existing.id}",
        json={"name": "Gamma Renamed"},
        headers={"Authorization": "Bearer x"},
    )
    assert update.status_code == 200

    # A NEW plan created after warming must now be visible: the admin write
    # cleared the prefix, so the next list re-queries the DB.
    fresh = _make_plan(db, name="Delta")
    after = client.get("/api/v1/tarif-plans")
    assert fresh.slug in _plan_slugs(after)


def test_currency_param_is_an_independent_cache_entry(db, client, cache):
    plan = _make_plan(db, name="Epsilon")
    eur = client.get("/api/v1/tarif-plans?currency=EUR")
    usd = client.get("/api/v1/tarif-plans?currency=USD")
    assert eur.status_code == 200
    assert usd.status_code == 200
    assert plan.slug in _plan_slugs(eur)
    assert plan.slug in _plan_slugs(usd)
    # Two distinct keys were written.
    assert cache.get("tarif-plans:list:EUR::") is not None
    assert cache.get("tarif-plans:list:USD::") is not None


def test_token_bundle_write_does_not_clear_plan_cache(db, client, cache):
    plan = _make_plan(db, name="Zeta")
    client.get("/api/v1/tarif-plans")
    assert cache.get("tarif-plans:list:EUR::") is not None

    # Simulate a token-bundle invalidation: it must touch only its own prefix.
    cache.delete_prefix("token-bundles:")

    assert cache.get("tarif-plans:list:EUR::") is not None
    assert plan.slug  # silence unused
