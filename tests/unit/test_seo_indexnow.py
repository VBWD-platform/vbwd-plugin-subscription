"""S154 D4 — subscription pings cms's IndexNow seam on tarif-plan change.

Unit level: the cms import is monkeypatched (no DB, no cms source needed), so
these run against the per-plugin isolated CI (subscription without cms) too,
where the import naturally raises ``ImportError`` and every assertion still
holds (no-op, never a crash).

Engineering requirements (binding, restated): TDD-first · DevOps-first · SOLID ·
DI · DRY · Liskov · clean code · NO OVERENGINEERING.
"""
import logging
from unittest.mock import MagicMock

from plugins.subscription.subscription.services import seo_indexnow


def _switch_on(monkeypatch, enabled=True):
    monkeypatch.setattr(seo_indexnow, "tarif_plans_indexing_enabled", lambda: enabled)


def _fixed_plans_path(monkeypatch, path="/plans"):
    monkeypatch.setattr(seo_indexnow, "public_plans_path", lambda: path)


def _fake_cms_ping(monkeypatch):
    ping = MagicMock()
    monkeypatch.setattr(seo_indexnow, "_import_ping_indexnow_urls", lambda: ping)
    return ping


def test_notify_plan_changed_pings_the_plan_path_when_switch_on(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, True)
    _fixed_plans_path(monkeypatch, "/plans")

    seo_indexnow.notify_plan_changed("gold-plan")

    ping.assert_called_once_with(["/plans/gold-plan", "/plans"])


def test_notify_plan_changed_noop_when_switch_off(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, False)
    _fixed_plans_path(monkeypatch, "/plans")

    seo_indexnow.notify_plan_changed("gold-plan")

    ping.assert_not_called()


def test_notify_plan_changed_noop_without_a_slug(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, True)
    _fixed_plans_path(monkeypatch, "/plans")

    seo_indexnow.notify_plan_changed("")
    seo_indexnow.notify_plan_changed(None)

    ping.assert_not_called()


def test_notify_plan_changed_also_pings_the_plan_list_url(monkeypatch):
    """The list page's content changes too (a new/removed row), per D10."""
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, True)
    _fixed_plans_path(monkeypatch, "/plans")

    seo_indexnow.notify_plan_changed("gold-plan")

    ping.assert_called_once_with(["/plans/gold-plan", "/plans"])


def test_notify_plan_changed_respects_the_configured_plans_slug(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, True)
    _fixed_plans_path(monkeypatch, "/pricing")

    seo_indexnow.notify_plan_changed("gold-plan")

    ping.assert_called_once_with(["/pricing/gold-plan", "/pricing"])


def test_notify_plan_renamed_pings_old_and_new_detail_plus_list(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, True)
    _fixed_plans_path(monkeypatch, "/plans")

    seo_indexnow.notify_plan_renamed("old-plan", "new-plan")

    ping.assert_called_once_with(["/plans/old-plan", "/plans/new-plan", "/plans"])


def test_notify_plan_renamed_noop_when_switch_off(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, False)
    _fixed_plans_path(monkeypatch, "/plans")

    seo_indexnow.notify_plan_renamed("old-plan", "new-plan")

    ping.assert_not_called()


def test_notify_plan_renamed_noop_without_any_slug(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, True)
    _fixed_plans_path(monkeypatch, "/plans")

    seo_indexnow.notify_plan_renamed("", None)

    ping.assert_not_called()


def test_noop_when_cms_is_not_importable(monkeypatch):
    def _missing_cms():
        raise ImportError("No module named 'plugins.cms'")

    monkeypatch.setattr(seo_indexnow, "_import_ping_indexnow_urls", _missing_cms)
    _switch_on(monkeypatch, True)
    _fixed_plans_path(monkeypatch, "/plans")

    # Never raises — the plugin keeps working with cms absent/disabled.
    seo_indexnow.notify_plan_changed("gold-plan")


def test_ping_failure_is_swallowed(monkeypatch, caplog):
    ping = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(seo_indexnow, "_import_ping_indexnow_urls", lambda: ping)
    _switch_on(monkeypatch, True)
    _fixed_plans_path(monkeypatch, "/plans")

    with caplog.at_level(logging.WARNING):
        seo_indexnow.notify_plan_changed("gold-plan")

    assert caplog.records
