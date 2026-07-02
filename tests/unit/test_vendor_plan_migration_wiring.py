"""Vendor-mode — the ``subscription_tarif_plan.vendor_id`` migration is wired
into the subscription plugin's own chain.

The migration anchors on the subscription plugin's prior head
(``20260625_sub_payment_method``, the S103.2a payment-method column) and adds a
nullable, indexed ``vendor_id`` UUID FK → ``vbwd_user`` (ON DELETE SET NULL).
"""
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # plugins/subscription
MIGRATION = PLUGIN_ROOT / "migrations/versions/20260701_sub_plan_vendor_id.py"

ALEMBIC_VERSION_NUM_MAXLEN = 32


def test_migration_exists_and_chains_off_subscription_prior_head():
    src = MIGRATION.read_text()
    revision = re.search(r'^revision = "([^"]+)"', src, re.M).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', src, re.M).group(1)
    assert revision == "20260701_sub_plan_vendor_id"
    # Anchors on subscription's own prior head (the S103.2a payment-method col).
    assert down == "20260625_sub_payment_method"
    assert len(revision) <= ALEMBIC_VERSION_NUM_MAXLEN


def test_migration_adds_nullable_indexed_vendor_id_fk():
    src = MIGRATION.read_text()
    assert "subscription_tarif_plan" in src
    assert "vendor_id" in src
    assert "vbwd_user" in src
    assert "SET NULL" in src
    assert "create_index" in src
