"""S43.4 — prefix the subscription tables `vbwd_*` → `subscription_*`.

These tables carry the CORE `vbwd_` prefix because subscription was extracted
from core (Sprint 11); they are a plugin, so S43 renames them to the plugin
prefix. 7 tables incl. 2 many-to-many association tables:

    vbwd_subscription               → subscription_record
    vbwd_addon                      → subscription_addon
    vbwd_addon_subscription         → subscription_addon_subscription
    vbwd_tarif_plan                 → subscription_tarif_plan
    vbwd_tarif_plan_category        → subscription_tarif_plan_category
    vbwd_addon_tarif_plans          → subscription_addon_tarif_plans
    vbwd_tarif_plan_category_plans  → subscription_tarif_plan_category_plans

Cross-plugin: `ghrm_software_package.tarif_plan_id` FK → `vbwd_tarif_plan` is
created by the monolith BEFORE this runs, so it auto-follows the rename in
Postgres; the ghrm model FK string is updated in lockstep. The `Subscription`
class is intentionally kept (table `subscription_record`) — module-scoped, like
`Booking`→booking_reservation / `TossCashReceipt`→toss_payments_cash_receipts.

PRESERVES DATA: pure `ALTER TABLE … RENAME` (+ dependent renames), no
drop/recreate. Runs on PROD via `deploy.sh --migrate` in CI: guarded +
idempotent.
"""
import sqlalchemy as sa
from alembic import op

revision = "20260531_subscription_prefix"
down_revision = "20260523_1000_sub_baseline"
branch_labels = None
depends_on = None

_RENAMES = {
    "vbwd_subscription": "subscription_record",
    "vbwd_addon": "subscription_addon",
    "vbwd_addon_subscription": "subscription_addon_subscription",
    "vbwd_tarif_plan": "subscription_tarif_plan",
    "vbwd_tarif_plan_category": "subscription_tarif_plan_category",
    "vbwd_addon_tarif_plans": "subscription_addon_tarif_plans",
    "vbwd_tarif_plan_category_plans": "subscription_tarif_plan_category_plans",
}


def _table_exists(conn, name: str) -> bool:
    return sa.inspect(conn).has_table(name)


def _rename_dependents(conn, table: str, frm: str, to: str) -> None:
    constraints = (
        conn.execute(
            sa.text(
                "SELECT conname FROM pg_constraint WHERE conrelid = to_regclass(:t)"
            ),
            {"t": table},
        )
        .scalars()
        .all()
    )
    for name in constraints:
        if frm in name:
            op.execute(
                f'ALTER TABLE "{table}" RENAME CONSTRAINT "{name}" '
                f'TO "{name.replace(frm, to, 1)}"'
            )
    plain_indexes = (
        conn.execute(
            sa.text(
                "SELECT i.relname FROM pg_index x "
                "JOIN pg_class i ON i.oid = x.indexrelid "
                "WHERE x.indrelid = to_regclass(:t) "
                "AND x.indexrelid NOT IN "
                "(SELECT conindid FROM pg_constraint WHERE conindid <> 0)"
            ),
            {"t": table},
        )
        .scalars()
        .all()
    )
    for name in plain_indexes:
        if frm in name:
            op.execute(f'ALTER INDEX "{name}" RENAME TO "{name.replace(frm, to, 1)}"')


def upgrade() -> None:
    conn = op.get_bind()
    for old, new in _RENAMES.items():
        if _table_exists(conn, old) and not _table_exists(conn, new):
            op.rename_table(old, new)
            _rename_dependents(conn, new, old, new)


def downgrade() -> None:
    conn = op.get_bind()
    for old, new in _RENAMES.items():
        if _table_exists(conn, new) and not _table_exists(conn, old):
            _rename_dependents(conn, new, new, old)
            op.rename_table(new, old)
