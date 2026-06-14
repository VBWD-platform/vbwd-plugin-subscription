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

    # Build the full schema exactly ONCE for the whole session, resetting the
    # public schema first (clearing any table or ENUM type left by a prior
    # crashed run or a sibling suite sharing this ``*_test`` DB). A per-test
    # create_all()/drop_all() strands standalone PG ENUM types and races other
    # suites on the shared catalog — see vbwd/testing/integration_db.py. Each
    # test then isolates by TRUNCATE-ing data, not by dropping the schema.
    with flask_app.app_context():
        from vbwd.extensions import db as _db
        from vbwd.testing.integration_db import reset_schema_and_create_all

        # Importing the package registers TarifPlan, Subscription, AddOn etc.
        # so the one-time create_all() builds the full subscription table set.
        import plugins.subscription.subscription.models  # noqa: F401

        reset_schema_and_create_all(_db)

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

    The schema is built once per session in the ``app`` fixture; the shared
    helper truncates every table and re-seeds the canonical RBAC role rows.
    """
    from vbwd.extensions import db as _db

    with app.app_context():
        from vbwd.testing.integration_db import truncate_all_tables

        truncate_all_tables(_db)
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
