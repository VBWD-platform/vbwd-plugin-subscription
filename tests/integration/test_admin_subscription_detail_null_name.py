"""Regression — GET /api/v1/admin/subscriptions/<id> must not 500 on NULL name.

``UserDetails.first_name`` / ``last_name`` are nullable columns. The admin
detail route enriches the payload with a ``user_name``; when a user has a
``details`` row but a NULL first_name or last_name, the old raw string
concatenation (``first_name + " " + last_name``) raised
``TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'`` and
produced a deterministic HTTP 500 (live on prod).

The fix routes ``user_name`` through the existing ``UserDetails.full_name``
property, which joins only the non-empty parts. This test pins that the route
returns 200 and the safe joined name for a user whose last_name is NULL.
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
from vbwd.models.user import User
from vbwd.models.user_details import UserDetails
from plugins.subscription.subscription.models.subscription import Subscription
from plugins.subscription.subscription.models.tarif_plan import TarifPlan


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


def _make_user_with_partial_name(db):
    """User with a details row whose first_name is set but last_name is NULL —
    the exact shape that used to 500."""
    user = User(
        id=uuid4(),
        email=f"partial-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
    )
    db.session.add(user)
    db.session.flush()
    details = UserDetails(
        id=uuid4(),
        user_id=user.id,
        first_name="Alice",
        last_name=None,
    )
    db.session.add(details)
    db.session.commit()
    return user


def _make_subscription(db, *, user_id):
    plan = TarifPlan(
        id=uuid4(),
        name="NullNamePlan",
        slug=f"nullnameplan-{uuid4().hex[:8]}",
        price=Decimal("100.00"),
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
    )
    db.session.add(plan)
    db.session.flush()
    subscription = Subscription(
        id=uuid4(),
        user_id=user_id,
        tarif_plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
    )
    db.session.add(subscription)
    db.session.commit()
    return subscription


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


def test_admin_subscription_detail_handles_null_last_name(db, client, monkeypatch):
    admin = _make_admin(db)
    user = _make_user_with_partial_name(db)
    subscription = _make_subscription(db, user_id=user.id)
    _auth_as_admin(monkeypatch, admin)

    resp = client.get(
        f"/api/v1/admin/subscriptions/{subscription.id}",
        headers={"Authorization": "Bearer valid"},
    )

    assert resp.status_code == 200, resp.get_json()
    sub = resp.get_json()["subscription"]
    # Safe joined name: just the first_name, no trailing space, no crash.
    assert sub["user_name"] == "Alice"
    assert sub["user_email"] == user.email
