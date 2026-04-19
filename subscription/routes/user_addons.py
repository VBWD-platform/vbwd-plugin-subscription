"""Public add-on routes (for user checkout and catalog)."""
from flask import jsonify, g
from vbwd.middleware.auth import optional_auth
from plugins.subscription.subscription.repositories.addon_repository import AddOnRepository
from vbwd.extensions import db
from plugins.subscription.subscription.models import Subscription
from vbwd.models.enums import SubscriptionStatus
from plugins.subscription.subscription.routes import subscription_bp


@subscription_bp.route("/api/v1/addons/", methods=["GET"])
@optional_auth
def list_active_addons():
    """
    List active add-ons available to the current user.

    - Authenticated user with active subscription:
        → independent add-ons + add-ons bound to user's plan
    - Authenticated user without subscription / unauthenticated:
        → independent add-ons only

    Returns:
        200: List of available add-ons
    """
    addon_repo = AddOnRepository(db.session)

    plan_id = None
    if hasattr(g, "user_id"):
        # Find user's active subscription to get their plan
        subscription = (
            db.session.query(Subscription)
            .filter(
                Subscription.user_id == g.user_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
            .first()
        )
        if subscription:
            plan_id = subscription.tarif_plan_id

    addons = addon_repo.find_available_for_plan(plan_id)

    return jsonify({"addons": [addon.to_dict() for addon in addons]}), 200


@subscription_bp.route("/api/v1/addons/<addon_id>", methods=["GET"])
def get_addon(addon_id):
    """
    Get add-on details by ID (public catalog endpoint).

    Args:
        addon_id: UUID of the add-on

    Returns:
        200: Add-on details
        404: Add-on not found
    """
    try:
        addon_repo = AddOnRepository(db.session)
        addon = addon_repo.find_by_id(addon_id)
    except Exception:
        return jsonify({"error": "Add-on not found"}), 404

    if not addon:
        return jsonify({"error": "Add-on not found"}), 404

    return jsonify({"addon": addon.to_dict()}), 200
