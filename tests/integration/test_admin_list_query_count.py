"""S48.3 — N+1 guard for GET /api/v1/admin/subscriptions/.

The admin list enriches every row with the user email and plan name. Before
S48.3 this was done with a per-row ``find_by_id`` lookup (2N+1 queries). The
fix batch-fetches users and plans, so the number of SQL statements the route
emits must be a small constant independent of page size.

This is a pure-performance guard: it asserts the SQL *count* is bounded, and a
companion behavioural test pins the response shape so the enrichment is
unchanged.
"""
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy import event

from vbwd.models.enums import BillingPeriod, SubscriptionStatus, UserRole, UserStatus
from vbwd.models.user import User
from plugins.subscription.subscription.models.subscription import Subscription
from plugins.subscription.subscription.models.tarif_plan import TarifPlan


# Upper bound on SELECTs for one admin list page, independent of page size:
# the paginated query, the COUNT, the batch user fetch, the batch plan fetch,
# plus a small allowance for auth/middleware lookups on the request path.
MAX_LIST_SELECTS = 8


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


def _make_subscription(db, *, user_id, plan_id):
    subscription = Subscription(
        id=uuid4(),
        user_id=user_id,
        tarif_plan_id=plan_id,
        status=SubscriptionStatus.ACTIVE,
    )
    db.session.add(subscription)
    db.session.commit()
    return subscription


def _seed_subscriptions(db, count):
    """Seed ``count`` subscriptions, each for a distinct user and plan so the
    enrichment cannot accidentally dedupe its way out of an N+1."""
    for index in range(count):
        user = User(
            id=uuid4(),
            email=f"sub-user-{index}-{uuid4().hex[:8]}@example.com",
            password_hash="x",
            status=UserStatus.ACTIVE,
        )
        db.session.add(user)
        plan = _make_plan(db, name=f"Plan{index}")
        _make_subscription(db, user_id=user.id, plan_id=plan.id)
    db.session.commit()


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


@contextmanager
def _count_selects(db):
    counter = {"selects": 0}
    engine = db.session.get_bind()

    def _on_execute(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["selects"] += 1

    event.listen(engine, "after_cursor_execute", _on_execute)
    try:
        yield counter
    finally:
        event.remove(engine, "after_cursor_execute", _on_execute)


def _list_select_count(db, client, monkeypatch, *, page_size):
    admin = _make_admin(db)
    _seed_subscriptions(db, page_size)
    _auth_as_admin(monkeypatch, admin)
    db.session.expire_all()

    with _count_selects(db) as counter:
        resp = client.get(
            f"/api/v1/admin/subscriptions/?limit={page_size}",
            headers={"Authorization": "Bearer valid"},
        )
    assert resp.status_code == 200, resp.get_json()
    return counter["selects"], resp.get_json()


def test_admin_list_subscriptions_is_not_n_plus_one(db, client, monkeypatch):
    selects, payload = _list_select_count(db, client, monkeypatch, page_size=10)

    assert len(payload["subscriptions"]) == 10
    assert selects <= MAX_LIST_SELECTS, (
        f"admin subscription list emitted {selects} SELECTs for a 10-row page; "
        f"expected <= {MAX_LIST_SELECTS} (N+1 regression)"
    )


def test_admin_list_subscriptions_query_count_independent_of_page_size(
    db, client, monkeypatch
):
    small, _ = _list_select_count(db, client, monkeypatch, page_size=3)
    large, _ = _list_select_count(db, client, monkeypatch, page_size=15)

    # A bounded (O(1)) list must not scale its query count with page size.
    assert (
        large <= small + 1
    ), f"query count grew with page size ({small} -> {large}); list is N+1"


def test_admin_list_subscriptions_enrichment_unchanged(db, client, monkeypatch):
    admin = _make_admin(db)
    user = User(
        id=uuid4(),
        email=f"enrich-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
    )
    db.session.add(user)
    plan = _make_plan(db, name="EnrichPlan")
    _make_subscription(db, user_id=user.id, plan_id=plan.id)
    _auth_as_admin(monkeypatch, admin)

    resp = client.get(
        "/api/v1/admin/subscriptions/",
        headers={"Authorization": "Bearer valid"},
    )

    assert resp.status_code == 200, resp.get_json()
    rows = resp.get_json()["subscriptions"]
    target = next(r for r in rows if r["user_id"] == str(user.id))
    assert target["user_email"] == user.email
    assert target["plan_name"] == plan.name
    assert target["created_at"] is not None
