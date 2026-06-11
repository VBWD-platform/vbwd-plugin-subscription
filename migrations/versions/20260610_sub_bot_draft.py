"""S53.0 — bot checkout draft table.

Creates ``subscription_bot_checkout_draft``: the per-chat, server-side selection
the bot storefront accumulates (D8). It is a bag of generic line items
(``{item_type, item_id, quantity}`` — the core ``LineItemType`` vocabulary) plus
a one-time, TTL'd ``token`` minted on ``/checkout``. No prices, no identity are
ever persisted.

Anchors on the subscription plugin's own prior head so the migration resolves
with the subscription plugin alone (no cross-plugin anchor).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260610_sub_bot_draft"
down_revision = "20260608_sub_admin_idx"
branch_labels = None
depends_on = None

TABLE = "subscription_bot_checkout_draft"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("chat_ref", sa.String(length=255), nullable=False),
        sa.Column("line_items", sa.JSON(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint(
            "provider_id", "chat_ref", name="uq_bot_checkout_draft_chat"
        ),
        sa.UniqueConstraint("token", name="uq_bot_checkout_draft_token"),
    )
    op.create_index(
        "ix_subscription_bot_checkout_draft_token", TABLE, ["token"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_subscription_bot_checkout_draft_token", table_name=TABLE)
    op.drop_table(TABLE)
