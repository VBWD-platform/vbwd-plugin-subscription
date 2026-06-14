"""Test fixtures for subscription plugin tests.

Mirrors the pattern from plugins/cms/tests/conftest.py — session-scoped Flask app
bound to a `<dbname>_test` database, function-scoped `db` fixture that runs
create_all() / drop_all() around each test.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

os.environ["FLASK_ENV"] = "testing"
os.environ["TESTING"] = "true"


def _test_db_url() -> str:
    base = os.getenv("DATABASE_URL", "postgresql://vbwd:vbwd@postgres:5432/vbwd")
    prefix, _, dbname = base.rpartition("/")
    dbname = dbname.split("?")[0]
    return f"{prefix}/{dbname}_test"


def _ensure_test_db(url: str) -> None:
    from sqlalchemy import create_engine, text

    main_url = url.rsplit("/", 1)[0] + "/postgres"
    dbname = url.rsplit("/", 1)[1].split("?")[0]
    engine = create_engine(main_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def app():
    from vbwd.app import create_app

    url = _test_db_url()
    _ensure_test_db(url)
    test_config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": url,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "RATELIMIT_ENABLED": False,
        "RATELIMIT_STORAGE_URL": "memory://",
    }
    flask_app = create_app(test_config)
    _ensure_subscription_enabled(flask_app)

    # Build the full schema exactly ONCE for the whole session. A per-test
    # create_all()/drop_all() (the old approach) churns DDL on db.metadata,
    # whose table set differs per test file (each file imports a different
    # model subset). That stranded ENUM types (duplicate "userstatus"),
    # dropped shared tables another file needs, and deadlocked under the
    # concurrent DDL — so the whole suite could not run together. We instead
    # reset the public schema once (clearing any table or ENUM left by a prior
    # crashed run) and create_all() once; each test then isolates by
    # TRUNCATE-ing data, not by dropping the schema (mirrors plugins/cms).
    with flask_app.app_context():
        from sqlalchemy import text

        from vbwd.extensions import db as _db

        # Importing the package registers TarifPlan, Subscription, AddOn etc.
        # so the one-time create_all() builds the full subscription table set.
        import plugins.subscription.subscription.models  # noqa: F401

        # Reset the schema and create every table on the SAME fresh connection,
        # so create_all()'s checkfirst reflection sees the just-cleared catalog
        # (a separate pooled connection can carry a pre-DROP snapshot). Close
        # any session first so no idle transaction holds a lock against DROP.
        _db.session.remove()
        with _db.engine.connect() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
            connection.commit()
            _db.metadata.create_all(bind=connection)
            connection.commit()

    yield flask_app

    with flask_app.app_context():
        from vbwd.extensions import db as _db

        _db.engine.dispose()


@pytest.fixture
def client(app):
    """Flask test client (for route-level tests relocated from core)."""
    return app.test_client()


def _ensure_subscription_enabled(flask_app) -> None:
    """Enable the subscription plugin (and its ``email`` dependency) so
    ``on_enable()`` runs and registers the DI providers / handlers.

    The plugin's enabled state is otherwise read from persisted config, which is
    empty on a fresh CI test database — so without this the providers are never
    registered and the DI-provider regression test fails. Enabling here makes the
    fixture deterministic regardless of ambient state.
    """
    from vbwd.plugins.base import PluginStatus

    manager = getattr(flask_app, "plugin_manager", None)
    if manager is None:
        return
    with flask_app.app_context():
        for name in ("email", "subscription"):
            plugin = manager.get_plugin(name)
            if plugin is None or plugin.status == PluginStatus.ENABLED:
                continue
            try:
                manager.enable_plugin(name)
            except ValueError:
                # A dependency (e.g. the email plugin) may be absent in this
                # environment. on_enable()'s DI-provider registration doesn't
                # need it, so enable directly to keep the regression guard valid.
                if plugin.status == PluginStatus.INITIALIZED:
                    plugin.enable()


@pytest.fixture
def db(app):
    """Isolate each test by TRUNCATE-ing data (not dropping the schema).

    The schema is built once per session in the ``app`` fixture. Here we clear
    data between tests by reflecting the tables that actually exist and
    truncating them all in one statement on a dedicated short-lived connection
    (``engine.begin()``) — not on ``db.session``, which can carry an open
    transaction and deadlock against the TRUNCATE. Truncating on SETUP (not
    teardown) is robust against a prior test that left rows.
    """
    from sqlalchemy import inspect, text

    from vbwd.extensions import db as _db

    with app.app_context():
        _db.session.remove()
        table_names = inspect(_db.engine).get_table_names(schema="public")
        if table_names:
            quoted = ", ".join(f'public."{name}"' for name in table_names)
            with _db.engine.begin() as connection:
                connection.execute(
                    text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
                )
                if "vbwd_user_role" in table_names:
                    from sqlalchemy import insert as _insert
                    from vbwd.models.user_role import (
                        RoleDefinition as _RoleDefinition,
                        canonical_role_rows as _canonical_role_rows,
                    )

                    connection.execute(
                        _insert(_RoleDefinition.__table__), _canonical_role_rows()
                    )
        _seed_default_currency(_db)
        yield _db
        _db.session.remove()


def _seed_default_currency(_db) -> None:
    """Seed the baseline EUR currency so the ``PriceFactory`` resolves a code.

    S85.2: subscription pricing now goes through the core ``PriceFactory``, which
    reads the default currency from the catalog (S84). Plugin integration tests
    truncate the catalog between tests, so the baseline row is re-seeded here —
    through the model, never raw SQL.
    """
    from decimal import Decimal as _Decimal
    from uuid import uuid4 as _uuid4

    from vbwd.models.currency import Currency

    if not _db.session.query(Currency).filter_by(code="EUR").first():
        _db.session.add(
            Currency(
                id=_uuid4(),
                code="EUR",
                name="Euro",
                symbol="€",
                exchange_rate=_Decimal("1.0"),
                decimal_places=2,
            )
        )
        _db.session.commit()
