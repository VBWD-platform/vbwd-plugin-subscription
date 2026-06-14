"""S72.4 — the per-plan price-display-mode migration is wired into the
subscription plugin's own chain.

The migration anchors on the subscription plugin's prior head
(``20260611_sub_plan_tax``, the S72.3 plan↔tax join) and adds a nullable
``price_display_mode VARCHAR(8)`` column to ``subscription_tarif_plan``.
"""
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # plugins/subscription
MIGRATION = PLUGIN_ROOT / "migrations/versions/20260611_sub_plan_display_mode.py"

ALEMBIC_VERSION_NUM_MAXLEN = 32


def test_migration_exists_and_chains_off_subscription_prior_head():
    src = MIGRATION.read_text()
    revision = re.search(r'^revision = "([^"]+)"', src, re.M).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', src, re.M).group(1)
    assert revision == "20260611_sub_plan_display_mode"
    # Anchors on subscription's own prior head (the S72.3 plan↔tax migration).
    assert down == "20260611_sub_plan_tax"
    assert len(revision) <= ALEMBIC_VERSION_NUM_MAXLEN


def test_migration_adds_nullable_display_mode_column():
    src = MIGRATION.read_text()
    assert "subscription_tarif_plan" in src
    assert "price_display_mode" in src
