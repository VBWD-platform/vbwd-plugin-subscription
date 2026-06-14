"""S72.3 — the plan↔tax join-table migration is wired into the subscription
plugin's own chain.

The migration anchors on the subscription plugin's prior head (no cross-plugin
anchor) and creates ``subscription_tarif_plan_tax`` with an
``ON DELETE RESTRICT`` FK to the CORE ``vbwd_tax`` catalog.
"""
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # plugins/subscription
MIGRATION = PLUGIN_ROOT / "migrations/versions/20260611_sub_plan_tax.py"

ALEMBIC_VERSION_NUM_MAXLEN = 32


def test_migration_exists_and_chains_off_subscription_prior_head():
    src = MIGRATION.read_text()
    revision = re.search(r'^revision = "([^"]+)"', src, re.M).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', src, re.M).group(1)
    assert revision == "20260611_sub_plan_tax"
    # Anchors on subscription's own prior head (the bot-draft migration).
    assert down == "20260610_sub_bot_draft"
    assert len(revision) <= ALEMBIC_VERSION_NUM_MAXLEN


def test_migration_creates_join_table_with_restrict_fk():
    src = MIGRATION.read_text()
    assert "subscription_tarif_plan_tax" in src
    assert "vbwd_tax.id" in src
    assert "RESTRICT" in src
