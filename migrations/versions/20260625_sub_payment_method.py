"""S103.2a — persist the checkout-selected payment method on the subscription.

Adds a nullable ``payment_method`` column to ``subscription_record`` holding the
payment-method *code* the user picked at checkout (e.g. ``"token_balance"``).
Trial-end conversion (S103.2) resolves that code → the ``RecurringChargeProvider``
plugin and re-charges the saved method off-session.

Anchors on the subscription plugin's own prior head so the migration resolves
with the subscription plugin alone (no cross-plugin anchor).
"""
from alembic import op
import sqlalchemy as sa

revision = "20260625_sub_payment_method"
down_revision = "20260617_sub_link_tarif_plan_id_idx"
branch_labels = None
depends_on = None

TABLE = "subscription_record"
COLUMN = "payment_method"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
