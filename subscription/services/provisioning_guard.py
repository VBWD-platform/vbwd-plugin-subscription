"""Seat-count + token-balance provisioning guard (subscription-domain policy).

Registered on the core ``user_provisioning_guard_registry`` seam; core runs it
before persisting a new user and names no policy. The policy lives entirely
here: it reads the acting operator's ACTIVE/TRIALING subscription plan
``features`` JSON and enforces whichever of these keys are present (each absent
key disables its own check — a plan with none is default-open):

  - ``seats``                  hard cap on total ADMIN + SUPER_ADMIN users.
  - ``max_users``              hard cap on total USER-role users.
  - ``tokens_per_admin`` /
    ``tokens_per_superadmin`` /
    ``tokens_per_user``        token COST to create a user of that role.

Order: the hard caps run first (they veto before any token is spent); the token
cost is checked and debited last, on the shared session, atomic with the user
creation (see ``token_provisioning``).
"""
from typing import Optional

from vbwd.models.enums import UserRole
from vbwd.registries.user_provisioning_guard_registry import (
    UserProvisioningBlocked,
    UserProvisioningRequest,
)

from plugins.subscription.subscription.services.token_provisioning import (
    debit_operator_tokens,
    read_operator_balance,
)

# ── feature keys read from the plan's ``features`` JSON ───────────────────────
FEATURE_SEATS = "seats"
FEATURE_MAX_USERS = "max_users"
FEATURE_TOKENS_PER_ADMIN = "tokens_per_admin"
FEATURE_TOKENS_PER_SUPERADMIN = "tokens_per_superadmin"
FEATURE_TOKENS_PER_USER = "tokens_per_user"

# ── veto codes / call-to-action targets (fe wires the real hyperlinks) ────────
CODE_SEAT_LIMIT_REACHED = "SEAT_LIMIT_REACHED"
CODE_MAX_USERS_REACHED = "MAX_USERS_REACHED"
CODE_TOKENS_REQUIRED = "TOKENS_REQUIRED"

STATUS_FORBIDDEN = 403
STATUS_PAYMENT_REQUIRED = 402

_UPGRADE_ACTION_LABEL = "Upgrade plan"
_UPGRADE_ACTION_URL = "/checkout?tarif_plan_id=pro"
_BUY_TOKENS_ACTION_LABEL = "Buy tokens"
_BUY_TOKENS_ACTION_URL = "/dashboard/tokens"

_SEAT_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)

_TOKENS_FEATURE_BY_ROLE = {
    UserRole.SUPER_ADMIN: FEATURE_TOKENS_PER_SUPERADMIN,
    UserRole.ADMIN: FEATURE_TOKENS_PER_ADMIN,
    UserRole.USER: FEATURE_TOKENS_PER_USER,
}

_ROLE_LABELS = {
    UserRole.SUPER_ADMIN: "super-admin",
    UserRole.ADMIN: "admin",
    UserRole.USER: "user",
}


def enforce_provisioning_limits(request: UserProvisioningRequest) -> None:
    """Veto provisioning that exceeds the operator's plan seat/token limits."""
    if request.acting_user_id is None:
        # No operator context (system path) — do not enforce.
        return

    features = _resolve_active_plan_features(request)
    if not features:
        # No active subscription, or plan carries no limiting keys — default-open.
        return

    _enforce_seat_cap(request, features)
    _enforce_max_users_cap(request, features)
    _enforce_token_cost(request, features)


def _resolve_active_plan_features(request: UserProvisioningRequest) -> Optional[dict]:
    """The acting operator's ACTIVE/TRIALING plan ``features`` map, or ``None``.

    Reuses the plugin's own list lookup (the same ACTIVE/TRIALING predicate used
    elsewhere) and, when the operator holds more than one active subscription
    (e.g. an upgrade briefly overlaps the old plan and the new one), picks the
    LATEST deterministically by activation timestamp — "your newest plan's
    features apply". A plain ``.first()`` is non-deterministic across the overlap
    and could keep reading the stale, more-restrictive plan. ``tarif_plan`` is
    the backref on ``TarifPlan``.
    """
    from plugins.subscription.subscription.repositories.subscription_repository import (  # noqa: E501
        SubscriptionRepository,
    )

    subscriptions = SubscriptionRepository(request.session).find_active_by_user_list(
        request.acting_user_id
    )
    if not subscriptions:
        return None
    latest_subscription = max(subscriptions, key=_activation_sort_key)
    plan = latest_subscription.tarif_plan
    if plan is None:
        return None
    features = plan.features
    return features if isinstance(features, dict) else None


def _activation_sort_key(subscription):
    """Recency key for an active subscription: activation time, else creation.

    ``started_at`` is the activation timestamp; a trialing row may not have one
    yet, so fall back to ``created_at`` (always set). Later = more recent =
    wins.
    """
    return subscription.started_at or subscription.created_at


def _enforce_seat_cap(request: UserProvisioningRequest, features: dict) -> None:
    if request.role not in _SEAT_ROLES:
        return
    seats = features.get(FEATURE_SEATS)
    if seats is None:
        return
    current = _count_users_in_roles(request.session, _SEAT_ROLES)
    if current + 1 > seats:
        raise UserProvisioningBlocked(
            f"Seat limit reached ({current} of {seats} used). "
            "Upgrade your plan to add more admins.",
            code=CODE_SEAT_LIMIT_REACHED,
            status=STATUS_FORBIDDEN,
            action_label=_UPGRADE_ACTION_LABEL,
            action_url=_UPGRADE_ACTION_URL,
        )


def _enforce_max_users_cap(request: UserProvisioningRequest, features: dict) -> None:
    if request.role != UserRole.USER:
        return
    max_users = features.get(FEATURE_MAX_USERS)
    if max_users is None:
        return
    current = _count_users_in_roles(request.session, (UserRole.USER,))
    if current + 1 > max_users:
        raise UserProvisioningBlocked(
            f"User limit reached ({current} of {max_users} used). "
            "Upgrade your plan to add more users.",
            code=CODE_MAX_USERS_REACHED,
            status=STATUS_FORBIDDEN,
            action_label=_UPGRADE_ACTION_LABEL,
            action_url=_UPGRADE_ACTION_URL,
        )


def _enforce_token_cost(request: UserProvisioningRequest, features: dict) -> None:
    feature_key = _TOKENS_FEATURE_BY_ROLE.get(request.role)
    if feature_key is None:
        return
    cost = features.get(feature_key)
    if not cost:
        return
    balance = read_operator_balance(request.session, request.acting_user_id)
    role_label = _ROLE_LABELS.get(request.role, "user")
    if balance < cost:
        raise UserProvisioningBlocked(
            f"Not enough tokens to create this {role_label} "
            f"(need {cost}, have {balance}). Buy tokens to continue.",
            code=CODE_TOKENS_REQUIRED,
            status=STATUS_PAYMENT_REQUIRED,
            action_label=_BUY_TOKENS_ACTION_LABEL,
            action_url=_BUY_TOKENS_ACTION_URL,
        )
    debit_operator_tokens(
        request.session,
        request.acting_user_id,
        cost,
        f"Provisioning cost: create {role_label} user",
    )


def _count_users_in_roles(session, roles) -> int:
    """Count existing users holding any of ``roles`` via the core repository."""
    from vbwd.repositories.user_repository import UserRepository

    user_repository = UserRepository(session)
    return sum(len(user_repository.find_by_role(role)) for role in roles)
