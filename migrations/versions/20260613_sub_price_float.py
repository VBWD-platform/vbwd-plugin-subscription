"""S85.1 — subscription price storage migration + addon↔tax join (D4/D5/D6).

Three things, all on the subscription plugin's own chain (no cross-plugin
anchor):

* Widen ``subscription_tarif_plan.price`` and ``subscription_addon.price`` from
  ``Numeric(10, 2)`` to ``double precision`` (``db.Float``) — prices are full
  precision and never rounded in code (D4); rounding lives only at display.
* Drop the redundant ``currency`` column from both tables (D5) — the single
  source of truth for the operating currency is the global ``default_currency``
  core setting (S84).
* Drop the lossy ``price_float`` mirror from ``subscription_tarif_plan`` (D5) —
  the single ``price`` double is enough.
* Create ``subscription_addon_tax`` (D6) mirroring the S72.3 join-table shape so
  add-ons carry a ``taxes`` relationship like plans/products/resources; the
  ``tax_id`` FK is ``ON DELETE RESTRICT`` (a clean DB block, never a silent
  cascade) and ``addon_id`` is ``ON DELETE CASCADE``.

Anchors on the subscription plugin's own current head so the chain stays linear
and resolves with the subscription plugin alone. ``downgrade`` re-narrows the
prices to ``Numeric(10, 2)``, re-adds ``currency`` / ``price_float``, and drops
the join table.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260613_sub_price_float"
down_revision = "20260613_sub_drop_price_id"
branch_labels = None
depends_on = None

PLAN_TABLE = "subscription_tarif_plan"
ADDON_TABLE = "subscription_addon"
ADDON_TAX_TABLE = "subscription_addon_tax"


def upgrade() -> None:
    op.alter_column(
        PLAN_TABLE,
        "price",
        type_=sa.Float(),
        existing_type=sa.Numeric(10, 2),
        existing_nullable=True,
        postgresql_using="price::double precision",
    )
    op.alter_column(
        ADDON_TABLE,
        "price",
        type_=sa.Float(),
        existing_type=sa.Numeric(10, 2),
        existing_nullable=False,
        postgresql_using="price::double precision",
    )

    op.drop_column(PLAN_TABLE, "currency")
    op.drop_column(PLAN_TABLE, "price_float")
    op.drop_column(ADDON_TABLE, "currency")

    op.create_table(
        ADDON_TAX_TABLE,
        sa.Column(
            "addon_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subscription_addon.id", ondelete="CASCADE"),
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
    op.drop_table(ADDON_TAX_TABLE)

    op.add_column(
        ADDON_TABLE,
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default="EUR"
        ),
    )
    op.add_column(
        PLAN_TABLE,
        sa.Column("price_float", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        PLAN_TABLE,
        sa.Column("currency", sa.String(length=3), nullable=True, server_default="EUR"),
    )

    op.alter_column(
        ADDON_TABLE,
        "price",
        type_=sa.Numeric(10, 2),
        existing_type=sa.Float(),
        existing_nullable=False,
        postgresql_using="price::numeric(10,2)",
    )
    op.alter_column(
        PLAN_TABLE,
        "price",
        type_=sa.Numeric(10, 2),
        existing_type=sa.Float(),
        existing_nullable=True,
        postgresql_using="price::numeric(10,2)",
    )
