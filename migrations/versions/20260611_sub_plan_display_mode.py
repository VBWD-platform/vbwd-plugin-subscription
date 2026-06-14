"""S72.4 — per-plan netto/brutto price-display override.

Adds a nullable ``price_display_mode VARCHAR(8)`` column to
``subscription_tarif_plan``. ``NULL`` inherits the global
``prices_display_mode`` core setting; ``"netto"``/``"brutto"`` override it.

Anchors on the subscription plugin's own prior head (the S72.3 plan↔tax join)
so the migration resolves with the subscription plugin alone (no cross-plugin
anchor).
"""
from alembic import op
import sqlalchemy as sa

revision = "20260611_sub_plan_display_mode"
down_revision = "20260611_sub_plan_tax"
branch_labels = None
depends_on = None

TABLE = "subscription_tarif_plan"
COLUMN = "price_display_mode"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
