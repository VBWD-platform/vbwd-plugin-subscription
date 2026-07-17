"""Session-atomic token read/debit for the provisioning guard.

A provisioning guard must NOT commit: core commits AFTER the whole provisioning
transaction (user row + our debit) succeeds, and a later failure must roll our
debit back with the user creation.

Core's ``TokenService`` used to commit unconditionally, so this module debited
by hand on the shared session to withhold that commit — bypassing the service,
and with it every token-movement hook. Since S138.0 the service takes
``commit=False``, so both calls here are plain core ``TokenService`` calls: one
home for the ledger shape, hooks fire, and the commit still belongs to core.
"""
from uuid import UUID


def _as_uuid(value) -> UUID:
    """Coerce an id (str from ``g.user_id`` or a UUID) to ``UUID``."""
    return value if isinstance(value, UUID) else UUID(str(value))


def _token_service(session):
    """Build a core ``TokenService`` bound to the caller's shared session."""
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
    return _token_service(session).get_balance(_as_uuid(acting_user_id))


def debit_operator_tokens(
    session, acting_user_id, amount: int, description: str
) -> None:
    """Debit ``amount`` tokens on the SHARED session WITHOUT committing.

    ``commit=False`` composes the debit into the caller's transaction: the
    balance moves and the hooks fire on the open transaction, but core owns the
    commit — so a later failure rolls the debit back with the user creation.

    The caller has already verified the balance is sufficient; ``debit_tokens``
    re-checks and raises ``ValueError`` on a race.
    """
    from vbwd.models.enums import TokenTransactionType

    _token_service(session).debit_tokens(
        user_id=_as_uuid(acting_user_id),
        amount=amount,
        transaction_type=TokenTransactionType.USAGE,
        description=description,
        commit=False,
    )
