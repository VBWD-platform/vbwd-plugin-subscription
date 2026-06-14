"""S85.1 — the subscription price-storage migration is wired into the plugin's
own chain.

It anchors on the subscription plugin's prior head (no cross-plugin anchor),
widens ``price`` to ``Float``, drops ``currency`` (plan + addon) and the lossy
``price_float`` mirror (plan), and creates ``subscription_addon_tax`` with an
``ON DELETE RESTRICT`` FK to the CORE ``vbwd_tax`` catalog.
"""
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # plugins/subscription
MIGRATION = PLUGIN_ROOT / "migrations/versions/20260613_sub_price_float.py"

ALEMBIC_VERSION_NUM_MAXLEN = 32


def test_migration_exists_and_chains_off_subscription_prior_head():
    src = MIGRATION.read_text()
    revision = re.search(r'^revision = "([^"]+)"', src, re.M).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', src, re.M).group(1)
    assert revision == "20260613_sub_price_float"
    # Anchors on subscription's own prior head (the S85.1a price_id drop).
    assert down == "20260613_sub_drop_price_id"
    assert len(revision) <= ALEMBIC_VERSION_NUM_MAXLEN


def test_migration_widens_price_to_float_and_drops_dead_columns():
    src = MIGRATION.read_text()
    assert "sa.Float()" in src
    assert 'drop_column(PLAN_TABLE, "currency")' in src
    assert 'drop_column(PLAN_TABLE, "price_float")' in src
    assert 'drop_column(ADDON_TABLE, "currency")' in src


def test_migration_creates_addon_tax_join_with_restrict_fk():
    src = MIGRATION.read_text()
    assert "subscription_addon_tax" in src
    assert "subscription_addon.id" in src
    assert "vbwd_tax.id" in src
    assert "RESTRICT" in src
    assert "CASCADE" in src
