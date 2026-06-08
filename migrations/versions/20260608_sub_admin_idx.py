"""S48.3 — admin subscription list: index the filter/sort columns.

GET /api/v1/admin/subscriptions/ filters by ``status`` / ``user_id`` /
``tarif_plan_id`` and always sorts by ``created_at DESC``. ``status`` and
``user_id`` are already indexed (model-level); ``tarif_plan_id`` (a bare FK, no
auto-index in Postgres) and ``created_at`` are not. Under load the missing
``created_at`` index forces a full sort and the missing ``tarif_plan_id`` index
forces a seq scan on the plan filter — both contribute to the admin list's
tail latency (S48 load profile).

Pure index addition, no data change. Guarded + reversible.
"""
from alembic import op

revision = "20260608_sub_admin_idx"
down_revision = "20260531_subscription_prefix"
branch_labels = None
depends_on = None

TABLE = "subscription_record"
_INDEXES = (
    ("ix_subscription_record_tarif_plan_id", "tarif_plan_id"),
    ("ix_subscription_record_created_at", "created_at"),
)


def upgrade() -> None:
    for index_name, column in _INDEXES:
        op.execute(
            f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{TABLE}" ("{column}")'
        )


def downgrade() -> None:
    for index_name, _column in _INDEXES:
        op.execute(f'DROP INDEX IF EXISTS "{index_name}"')
