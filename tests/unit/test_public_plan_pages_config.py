"""S154 D10 — the public tarif-plan pages' config: ``plans_slug`` + ``seo_index_tarif_plans``.

Tarif plans get public, indexable pages at ``/<plans_slug>`` (list) and
``/<plans_slug>/<plan_slug>`` (detail). The base slug is operator-configurable
(default ``plans``) and never hardcoded: every reader goes through
``plugin_config`` (``DEFAULT_CONFIG`` overlaid with the live saved config). The
indexable switch (default on) drives the sitemap provider and the pages' robots.
fe-user reads both from the public config endpoint at install time.

Engineering requirements (binding, restated): TDD-first; DevOps-first;
SOLID/DI/DRY/Liskov; clean code; no overengineering; core untouched. Gate:
``bin/pre-commit-check.sh --plugin subscription --full``.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from plugins.subscription import DEFAULT_CONFIG, SubscriptionPlugin
from plugins.subscription.subscription.services.plugin_config import (
    plans_slug,
    public_plans_path,
    tarif_plans_indexing_enabled,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


class _ConfigStore:
    def __init__(self, saved):
        self._saved = saved

    def get_config(self, plugin_name):
        assert plugin_name == "subscription"
        return dict(self._saved)


def _app(saved=None):
    flask_app = Flask(__name__)
    setattr(flask_app, "config_store", _ConfigStore(saved or {}))
    return flask_app


def _admin_field_keys():
    admin_config = json.loads((PLUGIN_ROOT / "admin-config.json").read_text())
    return {field["key"] for tab in admin_config["tabs"] for field in tab["fields"]}


def test_defaults_ship_in_default_config_and_config_json():
    config = json.loads((PLUGIN_ROOT / "config.json").read_text())

    assert DEFAULT_CONFIG["plans_slug"] == "plans"
    assert DEFAULT_CONFIG["seo_index_tarif_plans"] is True
    assert config["plans_slug"] == "plans"
    assert config["seo_index_tarif_plans"] is True


def test_both_keys_are_editable_in_fe_admin():
    assert {"plans_slug", "seo_index_tarif_plans"} <= _admin_field_keys()


def test_plans_slug_defaults_to_plans():
    with _app().app_context():
        assert plans_slug() == "plans"
        assert public_plans_path() == "/plans"


def test_plans_slug_follows_the_live_config():
    with _app({"plans_slug": "pricing"}).app_context():
        assert public_plans_path() == "/pricing"


def test_a_slashed_plans_slug_is_normalised():
    with _app({"plans_slug": " /pricing/ "}).app_context():
        assert plans_slug() == "pricing"


def test_a_blank_plans_slug_falls_back_to_the_default():
    with _app({"plans_slug": "  "}).app_context():
        assert plans_slug() == "plans"


def test_indexing_switch_defaults_on_and_follows_the_live_config():
    with _app().app_context():
        assert tarif_plans_indexing_enabled() is True
    with _app({"seo_index_tarif_plans": False}).app_context():
        assert tarif_plans_indexing_enabled() is False


def test_the_plugin_declares_its_public_fe_user_route_base():
    with _app({"plans_slug": "pricing"}).app_context():
        assert SubscriptionPlugin().fe_user_route_prefixes() == ["/pricing"]


def test_declaring_the_route_base_needs_no_theme_plugin():
    # ``None`` in sys.modules makes any ``import plugins.theme`` raise ImportError.
    with patch.dict(sys.modules, {"plugins.theme": None}):
        with _app().app_context():
            assert SubscriptionPlugin().fe_user_route_prefixes() == ["/plans"]
