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
    """Create all subscription-related tables, yield, drop after the test."""
    from vbwd.extensions import db as _db

    with app.app_context():
        # Importing the package registers TarifPlan, Subscription, AddOn etc.
        # so create_all() builds the full set of subscription tables.
        import plugins.subscription.subscription.models  # noqa: F401

        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()
