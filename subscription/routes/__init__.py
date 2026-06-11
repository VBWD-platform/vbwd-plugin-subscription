"""Subscription plugin routes — single blueprint, split across modules.

All route modules import `subscription_bp` from this file and register
their endpoints on it. The plugin returns this blueprint from get_blueprint().
"""
from flask import Blueprint

subscription_bp = Blueprint("subscription", __name__)

# Import route modules to register their endpoints on subscription_bp.
# Each module uses: from plugins.subscription.subscription.routes import subscription_bp
from plugins.subscription.subscription.routes import (  # noqa: F401, E402
    user_subscriptions,
    user_plans,
    user_addons,
    user_checkout,
    admin_subscriptions,
    admin_plans,
    admin_addons,
    admin_categories,
    admin_user_addons,
    public_checkout_draft,
)
