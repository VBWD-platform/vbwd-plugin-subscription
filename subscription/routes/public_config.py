"""Public (no-auth) subscription config endpoint.

``GET /api/v1/subscription/config`` returns ONLY the explicitly-whitelisted
safe public keys fe-user needs to render the storefront/dashboard. Every value
is read through the shared ``plugin_config`` readers (DEFAULT_CONFIG overlaid
with the admin-saved ``config_store``), so no private/secret key can leak: only
keys present in ``_PUBLIC_CONFIG_READERS`` are ever echoed.

Public on purpose — these values are not secrets and an unauthenticated
dashboard/catalogue visitor may render against them. S154 D10: fe-user mounts
the public plan pages under ``plans_slug`` and sets their robots from
``seo_index_tarif_plans``.
"""
from flask import jsonify

from plugins.subscription.subscription.routes import subscription_bp
from plugins.subscription.subscription.services.plugin_config import (
    PLANS_SLUG_KEY,
    SEO_INDEX_TARIF_PLANS_KEY,
    dashboard_plans_widget_slug,
    plans_slug,
    tarif_plans_indexing_enabled,
)

# The only keys this endpoint is allowed to expose, each with its reader.
_PUBLIC_CONFIG_READERS = {
    "dashboard_plans_widget_slug": dashboard_plans_widget_slug,
    PLANS_SLUG_KEY: plans_slug,
    SEO_INDEX_TARIF_PLANS_KEY: tarif_plans_indexing_enabled,
}


@subscription_bp.route("/api/v1/subscription/config", methods=["GET"])
def public_subscription_config():
    """Return the whitelisted public subscription config values."""
    return jsonify({key: read() for key, read in _PUBLIC_CONFIG_READERS.items()})
