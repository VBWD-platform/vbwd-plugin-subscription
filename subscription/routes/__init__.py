"""Subscription plugin routes — single blueprint, split across modules.

All route modules import `subscription_bp` from this file and register
their endpoints on it. The plugin returns this blueprint from get_blueprint().
"""
from flask import Blueprint

subscription_bp = Blueprint("subscription", __name__)

# Import route modules to register their endpoints on subscription_bp.
# Each module uses: from plugins.subscription.subscription.routes import subscription_bp
from plugins.subscription.subscription.routes import user_subscriptions  # noqa: F401, E402
from plugins.subscription.subscription.routes import user_plans  # noqa: F401, E402
from plugins.subscription.subscription.routes import user_addons  # noqa: F401, E402
from plugins.subscription.subscription.routes import admin_subscriptions  # noqa: F401, E402
from plugins.subscription.subscription.routes import admin_plans  # noqa: F401, E402
from plugins.subscription.subscription.routes import admin_addons  # noqa: F401, E402
from plugins.subscription.subscription.routes import admin_categories  # noqa: F401, E402
