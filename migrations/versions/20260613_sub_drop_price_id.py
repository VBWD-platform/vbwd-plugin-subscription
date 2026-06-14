"""S85.1a — drop the dead ``subscription_tarif_plan.price_id`` FK + column.

The persisted core ``Price`` model (``vbwd_price``) is dead and being removed
(see the core ``20260613_1100_drop_vbwd_price`` migration). ``subscription_tarif_plan``
was the ONLY table that FK'd it. This migration drops that ``price_id`` column
(and its FK + index) so the cross-plugin FK is gone BEFORE the core table is
dropped. The legacy ``subscription_tarif_plan.price`` double remains the source
of truth until S85.2 routes pricing through the computed ``PriceFactory``.

Anchors on the subscription plugin's own prior head so the chain resolves with
the subscription plugin alone (no cross-plugin anchor). The ``downgrade`` re-adds
the ``price_id`` column + its index + the FK to ``vbwd_price`` (the core
migration's own ``downgrade`` recreates the bare ``vbwd_price`` table first).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260613_sub_drop_price_id"
down_revision = "20260611_sub_plan_display_mode"
branch_labels = None
depends_on = None

TABLE = "subscription_tarif_plan"
COLUMN = "price_id"
FK_NAME = "subscription_tarif_plan_price_id_fkey"
INDEX_NAME = "ix_subscription_tarif_plan_price_id"


def upgrade() -> None:
    # ``drop_column`` on PostgreSQL transparently drops the dependent FK
    # constraint and index, leaving the legacy ``price``/``currency`` columns
    # untouched.
    op.drop_column(TABLE, COLUMN)


def downgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, UUID(as_uuid=True), nullable=True),
    )
    op.create_index(INDEX_NAME, TABLE, [COLUMN], unique=False)
    op.create_foreign_key(
        FK_NAME,
        TABLE,
        "vbwd_price",
        [COLUMN],
        ["id"],
    )
