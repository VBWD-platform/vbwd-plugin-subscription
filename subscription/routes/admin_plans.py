"""Admin tariff plan management routes."""
import re
from vbwd.utils.datetime_utils import utcnow
from flask import jsonify, request
from decimal import Decimal
from sqlalchemy import func
from vbwd.middleware.auth import require_auth, require_admin, require_permission
from plugins.subscription.subscription.repositories.tarif_plan_repository import TarifPlanRepository
from plugins.subscription.subscription.repositories.subscription_repository import SubscriptionRepository
from vbwd.extensions import db
from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.models import Subscription
from vbwd.models.enums import SubscriptionStatus
from plugins.subscription.subscription.routes import subscription_bp


@subscription_bp.route("/api/v1/admin/tarif-plans/", methods=["GET"])
@require_auth
@require_admin
@require_permission("subscription.plans.view")
def admin_list_plans():
    """
    List all tariff plans including inactive ones.

    Query params:
        - include_inactive: bool (default true for admin)

    Returns:
        200: List of all plans
    """
    plan_repo = TarifPlanRepository(db.session)

    # Admin sees all plans, including inactive
    plans = plan_repo.find_all()

    # Batch-count active subscribers per plan (single query, no N+1)
    plan_ids = [plan.id for plan in plans]
    if plan_ids:
        counts = (
            db.session.query(
                Subscription.tarif_plan_id,
                func.count(Subscription.id).label("cnt"),
            )
            .filter(
                Subscription.tarif_plan_id.in_(plan_ids),
                Subscription.status.in_(
                    [
                        SubscriptionStatus.ACTIVE,
                        SubscriptionStatus.TRIALING,
                    ]
                ),
            )
            .group_by(Subscription.tarif_plan_id)
            .all()
        )
        count_map = {str(row.tarif_plan_id): row.cnt for row in counts}
    else:
        count_map = {}

    result = []
    for plan in plans:
        data = plan.to_dict()
        data["subscriber_count"] = count_map.get(str(plan.id), 0)
        result.append(data)

    return jsonify({"plans": result}), 200


@subscription_bp.route("/api/v1/admin/tarif-plans/", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_create_plan():
    """
    Create a new tariff plan.

    Body:
        - name: str (required)
        - description: str (optional)
        - price: decimal (required)
        - currency: str (default: EUR)
        - billing_period: str (monthly, yearly)
        - features: dict (optional)
        - is_active: bool (default: true)

    Returns:
        201: Created plan
        400: Validation error
    """
    data = request.get_json() or {}

    # Validate required fields
    if not data.get("name"):
        return jsonify({"error": "Name is required"}), 400
    if "price" not in data:
        return jsonify({"error": "Price is required"}), 400

    try:
        # Generate slug if not provided
        slug = data.get("slug")
        if not slug:
            slug = re.sub(r"[^a-z0-9]+", "-", data["name"].lower()).strip("-")

        price_decimal = Decimal(str(data["price"]))
        features = data.get("features", {})
        if isinstance(features, dict) and "default_tokens" not in features:
            features["default_tokens"] = 0

        plan = TarifPlan(
            name=data["name"],
            slug=slug,
            description=data.get("description", ""),
            price=price_decimal,
            price_float=float(price_decimal),
            currency=data.get("currency", "EUR"),
            billing_period=data.get("billing_period", "MONTHLY").upper(),
            features=features,
            trial_days=int(data.get("trial_days", 0)),
            is_active=data.get("is_active", True),
        )

        plan_repo = TarifPlanRepository(db.session)
        saved_plan = plan_repo.save(plan)

        return (
            jsonify(
                {"plan": saved_plan.to_dict(), "message": "Plan created successfully"}
            ),
            201,
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 400


@subscription_bp.route("/api/v1/admin/tarif-plans/<plan_id>", methods=["GET"])
@require_auth
@require_admin
@require_permission("subscription.plans.view")
def admin_get_plan(plan_id):
    """
    Get plan detail.

    Args:
        plan_id: UUID of the plan

    Returns:
        200: Plan details
        404: Plan not found
    """
    plan_repo = TarifPlanRepository(db.session)
    plan = plan_repo.find_by_id(plan_id)

    if not plan:
        return jsonify({"error": "Plan not found"}), 404

    return jsonify({"plan": plan.to_dict()}), 200


@subscription_bp.route("/api/v1/admin/tarif-plans/<plan_id>", methods=["PUT"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_update_plan(plan_id):
    """
    Update tariff plan details.

    Args:
        plan_id: UUID of the plan

    Body:
        - name: str (optional)
        - description: str (optional)
        - price: decimal (optional)
        - currency: str (optional)
        - billing_period: str (optional)
        - features: dict (optional)
        - is_active: bool (optional)

    Returns:
        200: Updated plan
        404: Plan not found
    """
    plan_repo = TarifPlanRepository(db.session)
    plan = plan_repo.find_by_id(plan_id)

    if not plan:
        return jsonify({"error": "Plan not found"}), 404

    data = request.get_json() or {}

    if "name" in data:
        plan.name = data["name"]
    if "description" in data:
        plan.description = data["description"]
    if "price" in data:
        plan.price = Decimal(str(data["price"]))
        plan.price_float = float(data["price"])
    if "currency" in data:
        plan.currency = data["currency"]
    if "billing_period" in data:
        plan.billing_period = data["billing_period"]
    if "features" in data:
        features = data["features"]
        if isinstance(features, dict) and "default_tokens" not in features:
            features["default_tokens"] = 0
        plan.features = features
    if "is_active" in data:
        plan.is_active = data["is_active"]
    if "trial_days" in data:
        plan.trial_days = int(data["trial_days"])

    saved_plan = plan_repo.save(plan)

    return jsonify({"plan": saved_plan.to_dict()}), 200


@subscription_bp.route("/api/v1/admin/tarif-plans/<plan_id>", methods=["DELETE"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_delete_plan(plan_id):
    """
    Delete a tariff plan.

    Args:
        plan_id: UUID of the plan

    Returns:
        200: Plan deleted
        404: Plan not found
        400: Cannot delete plan with active subscriptions
    """
    plan_repo = TarifPlanRepository(db.session)
    sub_repo = SubscriptionRepository(db.session)

    plan = plan_repo.find_by_id(plan_id)
    if not plan:
        return jsonify({"error": "Plan not found"}), 404

    # Check for active subscriptions
    subs, total = sub_repo.find_all_paginated(plan_id=plan_id, limit=1)
    if total > 0:
        return (
            jsonify(
                {
                    "error": "Cannot delete plan with existing subscriptions. Deactivate instead."
                }
            ),
            400,
        )

    plan_repo.delete(plan_id)

    return jsonify({"message": "Plan deleted successfully"}), 200


@subscription_bp.route("/api/v1/admin/tarif-plans/<plan_id>/deactivate", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_deactivate_plan(plan_id):
    """
    Deactivate a tariff plan.

    Args:
        plan_id: UUID of the plan

    Returns:
        200: Plan deactivated
        404: Plan not found
    """
    plan_repo = TarifPlanRepository(db.session)
    plan = plan_repo.find_by_id(plan_id)

    if not plan:
        return jsonify({"error": "Plan not found"}), 404

    plan.is_active = False
    saved_plan = plan_repo.save(plan)

    return jsonify({"plan": saved_plan.to_dict(), "message": "Plan deactivated"}), 200


@subscription_bp.route("/api/v1/admin/tarif-plans/<plan_id>/activate", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_activate_plan(plan_id):
    """
    Activate a tariff plan.

    Args:
        plan_id: UUID of the plan

    Returns:
        200: Plan activated
        404: Plan not found
    """
    plan_repo = TarifPlanRepository(db.session)
    plan = plan_repo.find_by_id(plan_id)

    if not plan:
        return jsonify({"error": "Plan not found"}), 404

    plan.is_active = True
    saved_plan = plan_repo.save(plan)

    return jsonify({"plan": saved_plan.to_dict(), "message": "Plan activated"}), 200


@subscription_bp.route("/api/v1/admin/tarif-plans/<plan_id>/archive", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_archive_plan(plan_id):
    """Archive (deactivate) a tariff plan. Alias for /deactivate for backwards compat."""
    return deactivate_plan(plan_id)


@subscription_bp.route("/api/v1/admin/tarif-plans/<plan_id>/copy", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_copy_plan(plan_id):
    """
    Create a copy of an existing tariff plan.

    Args:
        plan_id: UUID of the source plan

    Returns:
        201: New plan created
        404: Source plan not found
    """
    plan_repo = TarifPlanRepository(db.session)
    source_plan = plan_repo.find_by_id(plan_id)

    if not source_plan:
        return jsonify({"error": "Plan not found"}), 404

    # Create a copy with "(copy)" appended to the name
    # Generate a unique slug for the copy
    base_slug = source_plan.slug or re.sub(
        r"[^a-z0-9]+", "-", source_plan.name.lower()
    ).strip("-")
    new_slug = f"{base_slug}-copy-{utcnow().strftime('%Y%m%d%H%M%S')}"

    new_plan = TarifPlan(
        name=f"{source_plan.name} (copy)",
        slug=new_slug,
        description=source_plan.description,
        price=source_plan.price,
        price_float=source_plan.price_float,
        currency=source_plan.currency,
        billing_period=source_plan.billing_period,
        features=source_plan.features,
        trial_days=source_plan.trial_days,
        is_active=True,  # New copy is active by default
    )

    saved_plan = plan_repo.save(new_plan)

    return (
        jsonify({"plan": saved_plan.to_dict(), "message": "Plan copied successfully"}),
        201,
    )
