"""Vendor self-service tarif-plan routes (marketplace vendor-mode).

Gated behind the ``marketplace_enabled`` config flag AND the user-facing
``marketplace.vendor`` permission. A vendor owns the plans they create
(``vendor_id`` = their user id). When vendor-mode is off the route returns 403
(classic admin-only plan management). The permission is the central marketplace
plugin's convention; subscription never imports marketplace.
"""
import re
from decimal import Decimal, InvalidOperation

from flask import g, jsonify, request

from vbwd.extensions import db
from vbwd.middleware.auth import require_auth, require_user_permission

from plugins.subscription.subscription.cache_keys import invalidate_plan_cache
from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.models.tarif_plan import (
    validate_price_display_mode,
)
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)
from plugins.subscription.subscription.routes import subscription_bp
from plugins.subscription.subscription.services.plugin_config import marketplace_enabled


_BILLING_PERIODS = ("MONTHLY", "YEARLY", "ONE_TIME")


def _require_marketplace_enabled():
    """Return a 403 response tuple when vendor-mode is off, else ``None``."""
    if not marketplace_enabled():
        return jsonify({"error": "Vendor mode is not enabled"}), 403
    return None


def _load_owned_plan(repository, plan_id):
    """Load a plan and authorise vendor ownership.

    Returns ``(plan, None)`` when the calling vendor owns the plan, or
    ``(None, error_response)`` where ``error_response`` is a 404 (missing) or
    403 (owned by another vendor) tuple.
    """
    plan = repository.find_by_id(plan_id)
    if plan is None:
        return None, (jsonify({"error": "Plan not found"}), 404)
    if str(plan.vendor_id) != str(g.user_id):
        return None, (jsonify({"error": "You do not own this plan"}), 403)
    return plan, None


@subscription_bp.route("/api/v1/subscription/vendor/plans", methods=["POST"])
@require_auth
@require_user_permission("marketplace.vendor")
def vendor_create_plan():
    """Vendor self-service: create a tarif plan the calling vendor owns."""
    disabled = _require_marketplace_enabled()
    if disabled:
        return disabled

    data = request.get_json() or {}

    if not data.get("name"):
        return jsonify({"error": "Name is required"}), 400
    if "price" not in data:
        return jsonify({"error": "Price is required"}), 400

    billing_period = str(data.get("billing_period", "MONTHLY")).upper()
    if billing_period not in _BILLING_PERIODS:
        return (
            jsonify({"error": f"billing_period must be one of {_BILLING_PERIODS}"}),
            400,
        )

    try:
        price = float(Decimal(str(data["price"])))
    except (InvalidOperation, TypeError, ValueError):
        return jsonify({"error": "Price is not a valid number"}), 400

    slug = data.get("slug")
    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", data["name"].lower()).strip("-")

    features = data.get("features", {})
    if isinstance(features, dict) and "default_tokens" not in features:
        features["default_tokens"] = 0

    try:
        plan = TarifPlan(
            name=data["name"],
            slug=slug,
            description=data.get("description", ""),
            price=price,
            billing_period=billing_period,
            features=features,
            trial_days=int(data.get("trial_days", 0)),
            is_active=True,
            price_display_mode=validate_price_display_mode(
                data.get("price_display_mode")
            ),
            vendor_id=g.user_id,
        )
        saved_plan = TarifPlanRepository(db.session).save(plan)
        invalidate_plan_cache()
    except ValueError as validation_error:
        return jsonify({"error": str(validation_error)}), 400

    return jsonify({"plan": saved_plan.to_dict(), "message": "Plan created"}), 201


@subscription_bp.route("/api/v1/subscription/vendor/plans", methods=["GET"])
@require_auth
@require_user_permission("marketplace.vendor")
def vendor_list_plans():
    """Vendor self-service: list the plans the calling vendor owns."""
    disabled = _require_marketplace_enabled()
    if disabled:
        return disabled

    plans = TarifPlanRepository(db.session).find_by_vendor(g.user_id)
    return jsonify({"plans": [plan.to_dict() for plan in plans]}), 200


@subscription_bp.route("/api/v1/subscription/vendor/plans/<plan_id>", methods=["GET"])
@require_auth
@require_user_permission("marketplace.vendor")
def vendor_get_plan(plan_id):
    """Vendor self-service: read a single owned plan."""
    disabled = _require_marketplace_enabled()
    if disabled:
        return disabled

    plan, error = _load_owned_plan(TarifPlanRepository(db.session), plan_id)
    if error:
        return error

    return jsonify({"plan": plan.to_dict()}), 200


@subscription_bp.route("/api/v1/subscription/vendor/plans/<plan_id>", methods=["PUT"])
@require_auth
@require_user_permission("marketplace.vendor")
def vendor_update_plan(plan_id):
    """Vendor self-service: update an owned plan's editable fields."""
    disabled = _require_marketplace_enabled()
    if disabled:
        return disabled

    repository = TarifPlanRepository(db.session)
    plan, error = _load_owned_plan(repository, plan_id)
    if error:
        return error

    data = request.get_json() or {}

    if "billing_period" in data:
        billing_period = str(data["billing_period"]).upper()
        if billing_period not in _BILLING_PERIODS:
            return (
                jsonify({"error": f"billing_period must be one of {_BILLING_PERIODS}"}),
                400,
            )
        plan.billing_period = billing_period

    if "price" in data:
        try:
            plan.price = float(Decimal(str(data["price"])))
        except (InvalidOperation, TypeError, ValueError):
            return jsonify({"error": "Price is not a valid number"}), 400

    if "name" in data:
        plan.name = data["name"]
    if "slug" in data:
        plan.slug = data["slug"]
    if "description" in data:
        plan.description = data["description"]
    if "trial_days" in data:
        plan.trial_days = int(data["trial_days"])
    if "is_active" in data:
        plan.is_active = bool(data["is_active"])

    try:
        saved_plan = repository.save(plan)
        invalidate_plan_cache()
    except ValueError as validation_error:
        return jsonify({"error": str(validation_error)}), 400

    return jsonify({"plan": saved_plan.to_dict()}), 200


@subscription_bp.route(
    "/api/v1/subscription/vendor/plans/<plan_id>", methods=["DELETE"]
)
@require_auth
@require_user_permission("marketplace.vendor")
def vendor_delete_plan(plan_id):
    """Vendor self-service: delete an owned plan."""
    disabled = _require_marketplace_enabled()
    if disabled:
        return disabled

    repository = TarifPlanRepository(db.session)
    plan, error = _load_owned_plan(repository, plan_id)
    if error:
        return error

    repository.delete(plan.id)
    invalidate_plan_cache()
    return jsonify({"success": True}), 200
