"""Session-atomic token read/debit for the provisioning guard.

Why this module exists (the atomicity nuance):

Core's ``TokenService.debit_tokens`` decrements the balance through
``BaseRepository.save``, which calls ``session.commit()`` — it is NOT
session-atomic. A provisioning guard must NOT commit: core commits AFTER the
whole provisioning transaction (user row + our debit) succeeds, and a later
failure must roll our debit back with the user creation. So we:

  - READ the balance through the core ``TokenService.get_balance`` (a pure read,
    safe to reuse verbatim), and
  - DEBIT by mutating the balance row and appending a ``TokenTransaction`` on the
    SHARED session WITHOUT committing — matching how core debits (balance
    decrement + negative-amount transaction row), but leaving the commit to
    core. The single home for the ledger shape stays core's models; we only
    withhold the commit.
"""
from uuid import UUID, uuid4

from vbwd.models.enums import TokenTransactionType
from vbwd.models.user_token_balance import TokenTransaction, UserTokenBalance


def _as_uuid(value) -> UUID:
    """Coerce an id (str from ``g.user_id`` or a UUID) to ``UUID``."""
    return value if isinstance(value, UUID) else UUID(str(value))


def read_operator_balance(session, acting_user_id) -> int:
    """Return the operator's current token balance via core ``TokenService``."""
    from vbwd.repositories.token_bundle_purchase_repository import (
        TokenBundlePurchaseRepository,
    )
    from vbwd.repositories.token_repository import (
        TokenBalanceRepository,
        TokenTransactionRepository,
    )
    from vbwd.services.token_service import TokenService

    token_service = TokenService(
        TokenBalanceRepository(session),
        TokenTransactionRepository(session),
        TokenBundlePurchaseRepository(session),
    )
    return token_service.get_balance(_as_uuid(acting_user_id))


def debit_operator_tokens(
    session, acting_user_id, amount: int, description: str
) -> None:
    """Debit ``amount`` tokens on the SHARED session WITHOUT committing.

    Deliberately does NOT reuse ``TokenService.debit_tokens`` because that path
    self-commits (see the module docstring): committing here would make the
    debit survive even if the user creation later fails. Instead we mutate the
    balance row and append the negative-amount ledger entry on the caller's
    transaction — core commits (or rolls back) the whole unit atomically.

    The caller has already verified the balance is sufficient.
    """
    user_uuid = _as_uuid(acting_user_id)
    balance = (
        session.query(UserTokenBalance)
        .filter(UserTokenBalance.user_id == user_uuid)
        .first()
    )
    if balance is None or balance.balance < amount:
        # Defensive: the caller checks first, so reaching here means a race.
        raise ValueError("Insufficient token balance")

    balance.balance -= amount
    session.add(
        TokenTransaction(
            id=uuid4(),
            user_id=user_uuid,
            amount=-amount,
            transaction_type=TokenTransactionType.USAGE,
            description=description,
        )
    )
    session.flush()
