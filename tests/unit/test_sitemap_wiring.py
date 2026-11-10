"""Subscription → cms sitemap wiring (S154 D3/D10: cms is an OPTIONAL dependency).

Subscription registers its ``subscription-plans`` provider into cms's sitemap
registry on enable and removes it on disable whenever cms is installed; with cms
absent both are silent no-ops, so subscription keeps working stand-alone.

Engineering requirements (binding, restated): TDD-first; DevOps-first;
SOLID/DI/DRY/Liskov; clean code; no overengineering; core untouched.
"""
import sys
import types

from plugins.subscription import SubscriptionPlugin, cms_sitemap_registry

CMS_SEO_REGISTRY_MODULE = "plugins.cms.src.services.seo_registry"


class FakeSitemapRegistry(types.SimpleNamespace):
    """The two registry functions + ``SitemapEntry`` subscription relies on."""

    def __init__(self):
        super().__init__(providers={})
        self.SitemapEntry = object

    def register_sitemap_provider(self, provider):
        self.providers[provider.name] = provider

    def unregister_sitemap_provider(self, provider):
        self.providers.pop(provider.name, None)


def test_enable_registers_and_disable_unregisters_the_plan_provider():
    registry = FakeSitemapRegistry()
    plugin = SubscriptionPlugin()

    plugin._register_sitemap_provider(registry)

    provider = registry.providers["subscription-plans"]
    assert provider._entry_factory is registry.SitemapEntry

    plugin._unregister_sitemap_provider(registry)

    assert registry.providers == {}


def test_registry_is_none_when_cms_is_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, CMS_SEO_REGISTRY_MODULE, None)

    assert cms_sitemap_registry() is None


def test_wiring_is_a_no_op_without_cms():
    plugin = SubscriptionPlugin()

    plugin._register_sitemap_provider(None)
    plugin._unregister_sitemap_provider(None)
