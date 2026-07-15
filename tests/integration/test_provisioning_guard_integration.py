"""Integration tests for the seat-count + token-balance provisioning guard.

The subscription plugin registers ``enforce_provisioning_limits`` on the core
``user_provisioning_guard_registry`` seam. Core runs it before persisting a new
user, handing a ``UserProvisioningRequest``; the guard reads the acting
operator's ACTIVE/TRIALING subscription plan ``features`` JSON and enforces:

  - ``seats``            — hard cap on total ADMIN + SUPER_ADMIN users.
  - ``max_users``        — hard cap on total USER-role users.
  - ``tokens_per_*``     — token COST to create a user of that role (debited on
                           the shared session, atomic with the user creation).

A plan with none of these keys, or no active subscription, or no acting
operator, is default-open (no enforcement).
"""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from vbwd.models.enums import BillingPeriod, SubscriptionStatus, UserRole
from vbwd.models.user import User
from vbwd.models.user_token_balance import UserTokenBalance
from vbwd.registries.user_provisioning_guard_registry import UserProvisioningBlocked

from plugins.subscription.subscription.models import Subscription, TarifPlan
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)
from plugins.subscription.subscription.services.provisioning_guard import (
    enforce_provisioning_limits,
)
from plugins.subscription.subscription.services.token_provisioning import (
    read_operator_balance,
)


def _user(db, role: UserRole = UserRole.USER) -> User:
    user = User(email=f"guard-{uuid4().hex}@example.com", password_hash="x", role=role)
    db.session.add(user)
    db.session.flush()
    return user


def _plan(db, features: dict) -> TarifPlan:
    plan = TarifPlan(
        id=uuid4(),
        name=f"plan-{uuid4().hex}",
        slug=f"plan-{uuid4().hex}",
        description="plan",
        price=9.99,
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
        sort_order=0,
        features=features,
    )
    return TarifPlanRepository(db.session).save(plan)


def _active_subscription(db, user_id, plan_id, started_at=None) -> Subscription:
    subscription = Subscription(
        user_id=user_id,
        tarif_plan_id=plan_id,
        status=SubscriptionStatus.ACTIVE,
        started_at=started_at,
    )
    db.session.add(subscription)
    db.session.flush()
    return subscription


def _credit_balance(db, user_id, amount: int) -> None:
    db.session.add(UserTokenBalance(id=uuid4(), user_id=user_id, balance=amount))
    db.session.flush()


def _request(db, *, action="create", email=None, role, acting_user_id):
    from vbwd.registries.user_provisioning_guard_registry import (
        UserProvisioningRequest,
    )

    return UserProvisioningRequest(
        action=action,
        email=email or f"new-{uuid4().hex}@example.com",
        role=role,
        acting_user_id=acting_user_id,
        session=db.session,
    )


def _operator_with_plan(db, features: dict, role: UserRole = UserRole.ADMIN) -> User:
    operator = _user(db, role=role)
    plan = _plan(db, features)
    _active_subscription(db, operator.id, plan.id)
    return operator


# ── no acting operator ────────────────────────────────────────────────────────
def test_no_acting_operator_is_default_open(db):
    request = _request(db, role=UserRole.ADMIN, acting_user_id=None)
    # Must not raise — no operator context means no enforcement.
    enforce_provisioning_limits(request)


# ── seat cap ──────────────────────────────────────────────────────────────────
def test_seat_cap_blocks_when_full(db):
    operator = _operator_with_plan(db, {"seats": 3})
    # operator itself is one ADMIN; add two more to reach 3 total.
    _user(db, role=UserRole.ADMIN)
    _user(db, role=UserRole.SUPER_ADMIN)

    request = _request(db, role=UserRole.ADMIN, acting_user_id=str(operator.id))
    with pytest.raises(UserProvisioningBlocked) as excinfo:
        enforce_provisioning_limits(request)

    blocked = excinfo.value
    assert blocked.code == "SEAT_LIMIT_REACHED"
    assert blocked.status == 403
    assert blocked.action_label == "Upgrade plan"
    assert blocked.action_url


def test_seat_cap_allows_when_below(db):
    operator = _operator_with_plan(db, {"seats": 3})
    # operator itself is one ADMIN — only 2 admins including a second one.
    _user(db, role=UserRole.ADMIN)

    request = _request(db, role=UserRole.ADMIN, acting_user_id=str(operator.id))
    # 2 existing + 1 new = 3 <= 3 → proceeds.
    enforce_provisioning_limits(request)


def test_two_active_subscriptions_use_latest_plan_features(db):
    """During an upgrade the old + new plan overlap as two active subscriptions.

    The guard must deterministically apply the NEWEST plan's features (seats:5),
    not the stale seats:3 — regardless of the row order returned by the repo.
    """
    operator = _user(db, role=UserRole.ADMIN)
    old_plan = _plan(db, {"seats": 3})
    new_plan = _plan(db, {"seats": 5})
    base_time = datetime(2026, 7, 14, 12, 0, 0)

    # Create the NEWER subscription first so the repo's unordered read is likely
    # to surface it — proving we sort by activation time, not insertion order.
    _active_subscription(
        db, operator.id, new_plan.id, started_at=base_time + timedelta(days=1)
    )
    _active_subscription(db, operator.id, old_plan.id, started_at=base_time)

    # 3 admins already exist (operator + 2). Under the stale seats:3 a 4th admin
    # would be blocked; under the newer seats:5 it is allowed.
    _user(db, role=UserRole.ADMIN)
    _user(db, role=UserRole.ADMIN)

    request = _request(db, role=UserRole.ADMIN, acting_user_id=str(operator.id))
    # Must NOT raise — the newest plan (seats:5) governs.
    enforce_provisioning_limits(request)


def test_two_active_subscriptions_latest_is_order_independent(db):
    """Reversing the insertion order still deterministically picks seats:5."""
    operator = _user(db, role=UserRole.ADMIN)
    old_plan = _plan(db, {"seats": 3})
    new_plan = _plan(db, {"seats": 5})
    base_time = datetime(2026, 7, 14, 12, 0, 0)

    # Older row inserted first this time.
    _active_subscription(db, operator.id, old_plan.id, started_at=base_time)
    _active_subscription(
        db, operator.id, new_plan.id, started_at=base_time + timedelta(days=1)
    )

    _user(db, role=UserRole.ADMIN)
    _user(db, role=UserRole.ADMIN)

    request = _request(db, role=UserRole.ADMIN, acting_user_id=str(operator.id))
    enforce_provisioning_limits(request)


# ── token cost ────────────────────────────────────────────────────────────────
def test_token_cost_blocks_when_balance_short(db):
    operator = _operator_with_plan(db, {"tokens_per_admin": 10})
    _credit_balance(db, operator.id, 5)

    request = _request(db, role=UserRole.ADMIN, acting_user_id=str(operator.id))
    with pytest.raises(UserProvisioningBlocked) as excinfo:
        enforce_provisioning_limits(request)

    blocked = excinfo.value
    assert blocked.code == "TOKENS_REQUIRED"
    assert blocked.status == 402
    assert blocked.action_label == "Buy tokens"
    assert blocked.action_url == "/dashboard/tokens"


def test_token_cost_debits_on_shared_session_and_rolls_back(db):
    operator = _operator_with_plan(db, {"tokens_per_admin": 10})
    _credit_balance(db, operator.id, 100)
    db.session.commit()

    request = _request(db, role=UserRole.ADMIN, acting_user_id=str(operator.id))

    nested = db.session.begin_nested()
    enforce_provisioning_limits(request)
    # Debited on the shared session — visible to a same-session read.
    assert read_operator_balance(db.session, str(operator.id)) == 90

    # Rolling back the caller's transaction restores the debit (atomicity).
    nested.rollback()
    assert read_operator_balance(db.session, str(operator.id)) == 100


# ── max_users cap ─────────────────────────────────────────────────────────────
def test_max_users_cap_blocks_third_user(db):
    operator = _operator_with_plan(db, {"max_users": 2}, role=UserRole.ADMIN)
    _user(db, role=UserRole.USER)
    _user(db, role=UserRole.USER)

    request = _request(db, role=UserRole.USER, acting_user_id=str(operator.id))
    with pytest.raises(UserProvisioningBlocked) as excinfo:
        enforce_provisioning_limits(request)

    assert excinfo.value.code == "MAX_USERS_REACHED"
    assert excinfo.value.status == 403


def test_superadmin_uses_tokens_per_superadmin_cost(db):
    operator = _operator_with_plan(
        db, {"tokens_per_superadmin": 25}, role=UserRole.ADMIN
    )
    _credit_balance(db, operator.id, 10)

    request = _request(db, role=UserRole.SUPER_ADMIN, acting_user_id=str(operator.id))
    with pytest.raises(UserProvisioningBlocked) as excinfo:
        enforce_provisioning_limits(request)

    assert excinfo.value.code == "TOKENS_REQUIRED"
    assert excinfo.value.status == 402


# ── default-open (no relevant keys) ───────────────────────────────────────────
def test_plan_without_limit_keys_is_default_open(db):
    operator = _operator_with_plan(db, {"some_other_feature": True})

    request = _request(db, role=UserRole.ADMIN, acting_user_id=str(operator.id))
    # No seats / tokens / max_users keys → no enforcement.
    enforce_provisioning_limits(request)


def test_no_active_subscription_is_default_open(db):
    operator = _user(db, role=UserRole.ADMIN)

    request = _request(db, role=UserRole.ADMIN, acting_user_id=str(operator.id))
    enforce_provisioning_limits(request)
