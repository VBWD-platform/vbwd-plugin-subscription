"""S89 — index the ``tarif_plan_id`` of the two plan M2M link tables.

``subscription_tarif_plan_category_plans`` and ``subscription_addon_tarif_plans``
both carry a composite PK whose SECOND column is ``tarif_plan_id``, so the PK
index cannot serve a ``WHERE tarif_plan_id = ?`` probe. Deleting a parent
``subscription_tarif_plan`` then fires the ``ON DELETE CASCADE`` action
``DELETE FROM <link> WHERE tarif_plan_id = $1`` once per deleted plan, each a
**sequential scan** of the link heap → **O(N²)** on a bulk plan delete (the S89
t3 1M-row ``subscription_plans`` load-test reset hang — the same gap fixed for
``shop_product_category_link`` in 20260617_shop_link_product_id_idx).

Adds the missing btree index on each so the cascade becomes an index probe.
Anchors on the subscription plugin's own current head so the chain resolves with
the subscription plugin alone.
"""
from alembic import op

revision = "20260617_sub_link_tarif_plan_id_idx"
down_revision = "20260613_sub_price_float"
branch_labels = None
depends_on = None

_INDEXES = (
    (
        "ix_subscription_tarif_plan_category_plans_tarif_plan_id",
        "subscription_tarif_plan_category_plans",
    ),
    (
        "ix_subscription_addon_tarif_plans_tarif_plan_id",
        "subscription_addon_tarif_plans",
    ),
)
_COLUMN = "tarif_plan_id"


def upgrade() -> None:
    for index_name, table_name in _INDEXES:
        op.create_index(index_name, table_name, [_COLUMN])


def downgrade() -> None:
    for index_name, table_name in _INDEXES:
        op.drop_index(index_name, table_name=table_name)
