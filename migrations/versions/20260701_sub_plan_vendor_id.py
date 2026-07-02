"""Vendor-mode — add ``subscription_tarif_plan.vendor_id`` (nullable, indexed FK).

Adds the owning vendor's ``vbwd_user`` id to tarif plans. ``NULL`` is a
platform-owned plan (the classic behaviour). The FK is ``ON DELETE SET NULL`` so
removing a user reverts their plans to the platform rather than cascading a
catalog delete; a btree index backs the vendor's "my plans" filter.

Anchors on the subscription plugin's own current head so the chain resolves with
the subscription plugin alone (core stays standalone-resolvable).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260701_sub_plan_vendor_id"
down_revision = "20260625_sub_payment_method"
branch_labels = None
depends_on = None

_TABLE = "subscription_tarif_plan"
_COLUMN = "vendor_id"
_INDEX = "ix_subscription_tarif_plan_vendor_id"
_FK = "fk_subscription_tarif_plan_vendor_id_user"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, UUID(as_uuid=True), nullable=True))
    op.create_index(_INDEX, _TABLE, [_COLUMN])
    op.create_foreign_key(
        _FK,
        _TABLE,
        "vbwd_user",
        [_COLUMN],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(_FK, _TABLE, type_="foreignkey")
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, _COLUMN)
