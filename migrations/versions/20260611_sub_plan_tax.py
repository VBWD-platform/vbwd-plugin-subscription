"""S72.3 — plan↔tax M2M join table.

Creates ``subscription_tarif_plan_tax`` linking ``subscription_tarif_plan`` to
the CORE tax catalog (``vbwd_tax``). The ``tax_id`` FK is ``ON DELETE RESTRICT``
so deleting a tax that is assigned to a plan is rejected by the database (a
clean block, never a silent cascade); ``tarif_plan_id`` is ``ON DELETE CASCADE``
so deleting a plan tidies its own links.

Anchors on the subscription plugin's own prior head so the migration resolves
with the subscription plugin alone (no cross-plugin anchor).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260611_sub_plan_tax"
down_revision = "20260610_sub_bot_draft"
branch_labels = None
depends_on = None

TABLE = "subscription_tarif_plan_tax"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "tarif_plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subscription_tarif_plan.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tax_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vbwd_tax.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
