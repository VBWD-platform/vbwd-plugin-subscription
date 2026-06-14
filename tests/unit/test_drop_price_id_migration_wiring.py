"""S85.1a — the ``tarif_plan.price_id`` FK-drop migration is wired into the
subscription plugin's own chain.

The persisted core ``Price`` model (``vbwd_price``) is dead and being removed.
``subscription_tarif_plan`` was the only table that FK'd it. This migration
drops the ``price_id`` column (and its FK) from ``subscription_tarif_plan`` so
the FK is gone BEFORE the core ``vbwd_price`` table is dropped. The migration
anchors on the subscription plugin's own prior head (no cross-plugin anchor),
and its ``downgrade`` re-adds the ``price_id`` column + FK to ``vbwd_price``.
"""
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # plugins/subscription
MIGRATION = PLUGIN_ROOT / "migrations/versions/20260613_sub_drop_price_id.py"

ALEMBIC_VERSION_NUM_MAXLEN = 32


def test_migration_exists_and_chains_off_subscription_prior_head():
    src = MIGRATION.read_text()
    revision = re.search(r'^revision = "([^"]+)"', src, re.M).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', src, re.M).group(1)
    assert revision == "20260613_sub_drop_price_id"
    # Anchors on subscription's own prior head (the plan-display-mode migration),
    # so the chain resolves with the subscription plugin alone.
    assert down == "20260611_sub_plan_display_mode"
    assert len(revision) <= ALEMBIC_VERSION_NUM_MAXLEN


def test_upgrade_drops_price_id_column_and_fk():
    src = MIGRATION.read_text()
    assert "subscription_tarif_plan" in src
    assert "price_id" in src
    assert "drop_column" in src


def test_downgrade_re_adds_price_id_column_and_fk_to_vbwd_price():
    src = MIGRATION.read_text()
    # The downgrade owns the cross-plugin FK re-add (the core migration's own
    # downgrade only recreates the bare ``vbwd_price`` table).
    assert "vbwd_price" in src
    assert "add_column" in src
    assert "create_foreign_key" in src
