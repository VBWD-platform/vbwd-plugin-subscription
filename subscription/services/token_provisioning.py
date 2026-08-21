"""Session-atomic token read/debit for the provisioning guard.

Why this module exists (the atomicity nuance):

A provisioning guard must NOT commit its debit: core commits AFTER the whole
provisioning transaction (the new user row + our debit) succeeds, and a later
failure must roll our debit back together with the user creation. Since S138.0
core's ``TokenService`` movements take a ``commit`` flag, so we get exactly that
by composing through the seam with ``commit=False`` — the debit flushes and the
token-movement hooks fire on the SHARED session, but the commit stays with core.
The single home for the balance mutation and the ledger row is core's
``TokenService``; we only withhold the commit.
"""
from uuid import UUID

from vbwd.models.enums import TokenTransactionType


def _as_uuid(value) -> UUID:
    """Coerce an id (str from ``g.user_id`` or a UUID) to ``UUID``."""
    return value if isinstance(value, UUID) else UUID(str(value))


def _build_token_service(session):
    """Build a core ``TokenService`` bound to the caller's shared session.

    The 4th ``session`` argument is required post-S138.0: the service flushes
    the balance, its ledger row and the hooks on that session as one unit of
    work, and (for ``commit=False``) leaves the commit to the caller.
    """
    from vbwd.repositories.token_bundle_purchase_repository import (
        TokenBundlePurchaseRepository,
    )
    from vbwd.repositories.token_repository import (
        TokenBalanceRepository,
        TokenTransactionRepository,
    )
    from vbwd.services.token_service import TokenService

    return TokenService(
        TokenBalanceRepository(session),
        TokenTransactionRepository(session),
        TokenBundlePurchaseRepository(session),
        session,
    )


def read_operator_balance(session, acting_user_id) -> int:
    """Return the operator's current token balance via core ``TokenService``."""
    token_service = _build_token_service(session)
    return token_service.get_balance(_as_uuid(acting_user_id))


def debit_operator_tokens(
    session, acting_user_id, amount: int, description: str
) -> None:
    """Debit ``amount`` tokens on the SHARED session WITHOUT committing.

    Composes through ``TokenService.debit_tokens`` with ``commit=False`` so the
    debit rides the caller's transaction: core commits (or rolls back) the whole
    unit — the new user row plus this debit — atomically. ``debit_tokens``
    validates ``amount > 0`` and raises ``ValueError("Insufficient token
    balance")`` when the balance is short, the same message the caller catches,
    so the defensive check is preserved by the seam.

    The caller has already verified the balance is sufficient.
    """
    token_service = _build_token_service(session)
    token_service.debit_tokens(
        user_id=_as_uuid(acting_user_id),
        amount=amount,
        transaction_type=TokenTransactionType.USAGE,
        description=description,
        commit=False,
    )
