"""Admin route: a user's add-on subscriptions.

S50.3 — this used to live in core (`/admin/users/<id>/addons`) backed by the
``subscription_read_model`` core port. Add-on subscriptions are a subscription
domain concept, so the endpoint now lives in the subscription plugin. The
fe-admin user-detail "Add-Ons" tab reads it. It is guarded by ``users.view``
(the same permission the user-detail page already requires) so admins viewing
a user keep seeing the add-ons without needing a subscription permission.
"""
from flask import jsonify

from vbwd.middleware.auth import require_auth, require_admin, require_permission
from vbwd.repositories.user_repository import UserRepository
from vbwd.extensions import db
from plugins.subscription.subscription.routes import subscription_bp
from plugins.subscription.subscription.services.subscription_read_model import (
    SubscriptionReadModel,
)


@subscription_bp.route(
    "/api/v1/admin/subscription/users/<user_id>/addons", methods=["GET"]
)
@require_auth
@require_admin
@require_permission("users.view")
def admin_get_user_addons(user_id):
    """
    Get a user's add-on subscriptions with invoice data.

    Args:
        user_id: UUID of the user

    Returns:
        200: List of add-on subscriptions with invoice info
        404: User not found
    """
    user = UserRepository(db.session).find_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    addon_subscriptions = SubscriptionReadModel().user_addon_subscriptions(user_id)
    return jsonify({"addon_subscriptions": addon_subscriptions}), 200
