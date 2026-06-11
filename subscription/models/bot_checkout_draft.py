"""Bot checkout draft model (S53.0 / D8).

A per-chat, server-side selection accumulated by the bot storefront. It cannot
ride a browser localStorage cart across the bot⇄browser boundary, so it lives
here as a bag of **generic** line items (the core ``LineItemType`` vocabulary:
SUBSCRIPTION / ADD_ON / TOKEN_BUNDLE). On ``/checkout`` a random one-time
``token`` + ``expires_at`` are set and a draft link is returned; the public
draft-resolution endpoint recomputes names/prices from the live catalogs
(never trusts amounts) and the browser checkout takes over. The draft persists
only ``{item_type, item_id, quantity}`` — no prices, no identity.
"""
from vbwd.extensions import db
from vbwd.models.base import BaseModel


class BotCheckoutDraft(BaseModel):
    """A bot-storefront checkout draft owned by the ``subscription`` plugin."""

    __tablename__ = "subscription_bot_checkout_draft"

    # Provider-neutral chat identity (matches the bot-base ``ChatRef`` pair).
    provider_id = db.Column(db.String(64), nullable=False)
    chat_ref = db.Column(db.String(255), nullable=False)

    # The selection: list of {"item_type", "item_id", "quantity"} dicts using
    # the core ``LineItemType`` value vocabulary. No prices are ever stored.
    line_items = db.Column(db.JSON, nullable=False, default=list)

    # Single-use opaque token minted on /checkout; null while still accumulating.
    token = db.Column(db.String(64), nullable=True, unique=True, index=True)
    # TTL boundary for the minted token; null until /checkout.
    expires_at = db.Column(db.DateTime, nullable=True)
    # Set when the public endpoint resolves the token (single-use enforcement).
    redeemed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint(
            "provider_id",
            "chat_ref",
            name="uq_bot_checkout_draft_chat",
        ),
    )

    def to_dict(self) -> dict:
        """Serialize the draft (used by tests / debugging, not the public API)."""
        return {
            "id": str(self.id),
            "provider_id": self.provider_id,
            "chat_ref": self.chat_ref,
            "line_items": self.line_items or [],
            "token": self.token,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "redeemed_at": self.redeemed_at.isoformat() if self.redeemed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<BotCheckoutDraft(provider_id='{self.provider_id}', "
            f"chat_ref='{self.chat_ref}', items={len(self.line_items or [])})>"
        )
