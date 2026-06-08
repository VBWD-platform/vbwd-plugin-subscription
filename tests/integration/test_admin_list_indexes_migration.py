"""S48.3 — the admin-list index migration is well-formed and up/down/up clean.

Adds indexes for the columns the admin subscription list filters/sorts on
(``tarif_plan_id`` for the plan filter, ``created_at`` for the default sort).
``user_id`` and ``status`` are already indexed on the model.

The migration is the prod path; this test runs its ``upgrade`` / ``downgrade``
directly against the test connection (the tables already exist via the ``db``
fixture's ``create_all``) and asserts the indexes appear and disappear, then
reappear — proving the migration is reversible and idempotent-safe.
"""
import importlib.util
import re
from pathlib import Path

from sqlalchemy import inspect

PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # plugins/subscription
MIGRATION = PLUGIN_ROOT / "migrations/versions/20260608_sub_admin_idx.py"

ALEMBIC_VERSION_NUM_MAXLEN = 32
TABLE = "subscription_record"
EXPECTED_INDEX_COLUMNS = {("tarif_plan_id",), ("created_at",)}


def _load_migration():
    spec = importlib.util.spec_from_file_location("sub_admin_idx", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _index_column_sets(connection):
    indexes = inspect(connection).get_indexes(TABLE)
    return {tuple(index["column_names"]) for index in indexes}


def test_migration_revision_well_formed():
    src = MIGRATION.read_text()
    revision = re.search(r'^revision = "([^"]+)"', src, re.M).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', src, re.M).group(1)
    assert revision == "20260608_sub_admin_idx"
    assert len(revision) <= ALEMBIC_VERSION_NUM_MAXLEN
    # Anchors on the subscription plugin's own prior head (resolvable with the
    # subscription plugin alone).
    assert down == "20260531_subscription_prefix"


def test_migration_up_down_up_creates_admin_indexes(db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _load_migration()
    connection = db.session.connection()
    context = MigrationContext.configure(connection)

    # ``Operations.context`` installs the module-level ``op`` proxy so the
    # migration's bare ``op.create_index`` / ``op.drop_index`` calls resolve
    # against this connection.
    with Operations.context(context):
        # create_all() already declares the model-level indexes; drop them first
        # so the migration's upgrade is what creates them in this test.
        module.downgrade()
        before = _index_column_sets(connection)
        assert not (EXPECTED_INDEX_COLUMNS & before)

        module.upgrade()
        after_up = _index_column_sets(connection)
        assert EXPECTED_INDEX_COLUMNS <= after_up

        module.downgrade()
        after_down = _index_column_sets(connection)
        assert not (EXPECTED_INDEX_COLUMNS & after_down)

        module.upgrade()
        after_up_again = _index_column_sets(connection)
        assert EXPECTED_INDEX_COLUMNS <= after_up_again

    db.session.rollback()
