"""BotCheckoutDraft repository (S53.0 / D8).

The single data-access home for the bot storefront's per-chat draft. All draft
persistence flows through here (no raw SQL anywhere in the storefront).
"""
from typing import Optional

from vbwd.repositories.base import BaseRepository
from plugins.subscription.subscription.models import BotCheckoutDraft


class BotCheckoutDraftRepository(BaseRepository[BotCheckoutDraft]):
    """Repository for the bot checkout draft."""

    def __init__(self, session):
        super().__init__(session=session, model=BotCheckoutDraft)

    def find_by_chat(
        self, provider_id: str, chat_ref: str
    ) -> Optional[BotCheckoutDraft]:
        """The active draft for a (provider, chat) pair, if any."""
        return (
            self._session.query(BotCheckoutDraft)
            .filter(
                BotCheckoutDraft.provider_id == provider_id,
                BotCheckoutDraft.chat_ref == chat_ref,
            )
            .first()
        )

    def find_by_token(self, token: str) -> Optional[BotCheckoutDraft]:
        """The draft minted with ``token`` (single-use lookup), if any."""
        return (
            self._session.query(BotCheckoutDraft)
            .filter(BotCheckoutDraft.token == token)
            .first()
        )
