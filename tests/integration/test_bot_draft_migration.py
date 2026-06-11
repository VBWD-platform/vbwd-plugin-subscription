"""S53.0 — the bot-checkout-draft migration is well-formed + up/down/up clean.

Creates ``subscription_bot_checkout_draft``. The migration is the prod path; this
test runs its ``upgrade`` / ``downgrade`` directly against the test connection
and asserts the table appears, disappears, and reappears — proving it is
reversible. The revision anchors on the subscription plugin's own prior head so
it resolves with the subscription plugin alone.
"""
import importlib.util
import re
from pathlib import Path

from sqlalchemy import inspect

PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # plugins/subscription
MIGRATION = PLUGIN_ROOT / "migrations/versions/20260610_sub_bot_draft.py"

ALEMBIC_VERSION_NUM_MAXLEN = 32
TABLE = "subscription_bot_checkout_draft"


def _load_migration():
    spec = importlib.util.spec_from_file_location("sub_bot_draft", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_well_formed():
    src = MIGRATION.read_text()
    revision = re.search(r'^revision = "([^"]+)"', src, re.M).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', src, re.M).group(1)
    assert revision == "20260610_sub_bot_draft"
    assert len(revision) <= ALEMBIC_VERSION_NUM_MAXLEN
    assert down == "20260608_sub_admin_idx"


def test_migration_up_down_up(db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _load_migration()
    connection = db.session.connection()
    context = MigrationContext.configure(connection)

    with Operations.context(context):
        # create_all() already built the table via the model; drop it first so
        # the migration's upgrade is what (re)creates it in this test.
        module.downgrade()
        assert TABLE not in inspect(connection).get_table_names()

        module.upgrade()
        assert TABLE in inspect(connection).get_table_names()
        columns = {col["name"] for col in inspect(connection).get_columns(TABLE)}
        assert {
            "provider_id",
            "chat_ref",
            "line_items",
            "token",
            "expires_at",
            "redeemed_at",
        } <= columns

        module.downgrade()
        assert TABLE not in inspect(connection).get_table_names()

        # Restore for the rest of the fixture teardown (drop_all expects it gone,
        # but recreate keeps create_all/drop_all symmetric across the session).
        module.upgrade()
        assert TABLE in inspect(connection).get_table_names()
