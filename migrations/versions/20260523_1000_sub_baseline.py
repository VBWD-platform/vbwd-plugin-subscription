"""Subscription plugin migration branch baseline (Sprint 03/S7, decision A1).

Establishes the subscription plugin's Alembic branch point so that ALL
future subscription schema changes live here
(`plugins/subscription/migrations/versions/`), per E4 and
`feedback_plugin_migrations_in_plugin`.

No-op by design: the subscription tables (vbwd_subscription, vbwd_tarif_plan,
vbwd_addon, vbwd_addon_subscription, vbwd_tarif_plan_category + the two m2m
tables) are created by the core monolith
`alembic/versions/20260403_1612_vbwd_all_tables.py`. Under decision (A) the
subscription model classes remain core-defined (shared domain — 6 plugins
depend on them), and the initial tables stay FK-entangled with
vbwd_user_invoice inside that monolith, so extracting the initial DDL here is
deferred (A2, prod-gated). This revision only chains the plugin's branch onto
the current head; future subscription migrations set their down_revision to
this id.

Revision ID: 20260523_1000_sub_baseline
Revises: 20260424_1015
Create Date: 2026-05-23

(Revision id kept <= 32 chars for the alembic_version.version_num column.)
"""

# revision identifiers, used by Alembic.
revision = "20260523_1000_sub_baseline"
down_revision = "20260424_1015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: initial subscription tables are owned by the core monolith
    # (decision A1). This revision is the plugin's migration branch baseline.
    pass


def downgrade() -> None:
    pass
