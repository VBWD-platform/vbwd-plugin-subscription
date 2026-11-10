"""Single home for reading the subscription plugin's runtime config (DRY).

Reads fresh from the shared ``config_store`` on every call (multi-worker safe,
admin changes take effect without restart) and falls back to the plugin's
``DEFAULT_CONFIG`` for any missing key. Mirrors the shop plugin's helper.
"""
from typing import Any, Dict

from flask import current_app


def subscription_config() -> Dict[str, Any]:
    """The merged subscription config: ``DEFAULT_CONFIG`` overlaid with saved values."""
    from plugins.subscription import DEFAULT_CONFIG

    merged = {**DEFAULT_CONFIG}
    config_store = getattr(current_app, "config_store", None)
    if config_store is not None:
        merged.update(config_store.get_config("subscription") or {})
    return merged


def marketplace_enabled() -> bool:
    """Whether vendor-mode (self-service vendor routes + checkout stamp) is on."""
    return bool(subscription_config().get("marketplace_enabled", False))


def dashboard_plans_widget_slug() -> str:
    """Slug of the TariffPlanCollection CMS widget for the user dashboard plans page."""
    return str(subscription_config().get("dashboard_plans_widget_slug", "") or "")


# S154 D10 — the public tarif-plan pages (fe-user, both frontend modes).
PLANS_SLUG_KEY = "plans_slug"
SEO_INDEX_TARIF_PLANS_KEY = "seo_index_tarif_plans"
PATH_SEPARATOR = "/"


def plans_slug() -> str:
    """The public plans base slug, without slashes; the default when left blank."""
    from plugins.subscription import DEFAULT_CONFIG

    configured = str(subscription_config().get(PLANS_SLUG_KEY) or "")
    return configured.strip().strip(PATH_SEPARATOR) or str(
        DEFAULT_CONFIG[PLANS_SLUG_KEY]
    )


def public_plans_path() -> str:
    """The root-relative path of the public plan list (``/<plans_slug>``)."""
    return f"{PATH_SEPARATOR}{plans_slug()}"


def tarif_plans_indexing_enabled() -> bool:
    """Whether the public plan pages are indexable — ``seo_index_tarif_plans``."""
    return bool(subscription_config()[SEO_INDEX_TARIF_PLANS_KEY])
