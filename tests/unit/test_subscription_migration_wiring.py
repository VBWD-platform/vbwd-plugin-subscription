"""S7/A1 — the subscription plugin owns an Alembic migration branch.

Guards the wiring: the plugin migrations dir is registered, the baseline
revision chains off the prior head, and its id fits the
alembic_version.version_num(32) column (the bug this test pins).
"""
import re
from pathlib import Path

# This test lives in the subscription plugin:
# <backend>/plugins/subscription/tests/unit/<this file>
PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # plugins/subscription
BACKEND_ROOT = PLUGIN_ROOT.parents[1]  # <backend>
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
BASELINE = PLUGIN_ROOT / "migrations/versions/20260523_1000_sub_baseline.py"

# Alembic's default alembic_version.version_num is VARCHAR(32).
ALEMBIC_VERSION_NUM_MAXLEN = 32


def test_subscription_migrations_dir_registered_in_alembic_ini():
    content = ALEMBIC_INI.read_text()
    assert "plugins/subscription/migrations/versions" in content


def test_baseline_revision_chains_off_a_core_revision():
    """Subscription is a foundational plugin: its baseline must anchor on a CORE
    revision (so core/other plugins never depend on subscription's ancestors),
    not on another plugin's migration."""
    src = BASELINE.read_text()
    revision = re.search(r'^revision = "([^"]+)"', src, re.M).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', src, re.M).group(1)
    assert revision == "20260523_1000_sub_baseline"
    # 20260406_1800 is a core revision (vbwd-backend/alembic/versions/).
    assert down == "20260406_1800"


def test_baseline_revision_id_fits_alembic_version_column():
    src = BASELINE.read_text()
    revision = re.search(r'^revision = "([^"]+)"', src, re.M).group(1)
    assert len(revision) <= ALEMBIC_VERSION_NUM_MAXLEN, (
        f"revision id {revision!r} ({len(revision)} chars) exceeds the "
        f"alembic_version.version_num({ALEMBIC_VERSION_NUM_MAXLEN}) column"
    )
