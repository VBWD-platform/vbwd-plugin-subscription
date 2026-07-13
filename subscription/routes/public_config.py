"""Public (no-auth) subscription config endpoint.

``GET /api/v1/subscription/config`` returns ONLY the explicitly-whitelisted
safe public keys fe-user needs to render the storefront/dashboard. Every value
is read through the shared ``subscription_config()`` helper (DEFAULT_CONFIG
overlaid with the admin-saved ``config_store``), so no private/secret key can
leak: only keys present in ``_PUBLIC_CONFIG_DEFAULTS`` are ever echoed.

Public on purpose — these values are not secrets and an unauthenticated
dashboard/catalogue visitor may render against them.
"""
from flask import jsonify

from plugins.subscription.subscription.routes import subscription_bp
from plugins.subscription.subscription.services.plugin_config import (
    subscription_config,
)

# The only keys this endpoint is allowed to expose, with their fallback values.
_PUBLIC_CONFIG_DEFAULTS = {
    "dashboard_plans_widget_slug": "",
}


@subscription_bp.route("/api/v1/subscription/config", methods=["GET"])
def public_subscription_config():
    """Return the whitelisted public subscription config values."""
    merged = subscription_config()
    return jsonify(
        {
            key: merged.get(key, default)
            for key, default in _PUBLIC_CONFIG_DEFAULTS.items()
        }
    )
