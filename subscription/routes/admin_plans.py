"""Admin tariff plan management routes."""
import re
from flask import jsonify, request
from decimal import Decimal
from sqlalchemy import func
from vbwd.middleware.auth import require_auth, require_admin, require_permission
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)
from plugins.subscription.subscription.repositories.subscription_repository import (
    SubscriptionRepository,
)
from plugins.subscription.subscription.services.tarif_plan_service import (
    TarifPlanService,
)
from vbwd.extensions import db
from vbwd.models.tax import Tax
from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.models import Subscription
from plugins.subscription.subscription.models.tarif_plan import (
    validate_price_display_mode,
)
from vbwd.models.enums import SubscriptionStatus
from plugins.subscription.subscription.cache_keys import invalidate_plan_cache
from plugins.subscription.subscription.routes import subscription_bp


class TaxAssignmentError(ValueError):
    """Raised when a requested ``tax_ids`` entry is unknown or inactive."""


def _resolve_active_taxes(tax_ids):
    """Resolve ``tax_ids`` to active core taxes, deduped and order-preserving.

    Raises ``TaxAssignmentError`` if any id is unknown or its tax is inactive.
    """
    deduped = list(dict.fromkeys(tax_ids))
    if not deduped:
        return []

    found = {
        str(tax.id): tax
        for tax in db.session.query(Tax).filter(Tax.id.in_(deduped)).all()
    }
    resolved = []
    for tax_id in deduped:
        tax = found.get(str(tax_id))
        if tax is None:
            raise TaxAssignmentError(f"Unknown tax: {tax_id}")
        if not tax.is_active:
            raise TaxAssignmentError(f"Tax is not active: {tax_id}")
        resolved.append(tax)
    return resolved


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
            price=float(price_decimal),
            billing_period=data.get("billing_period", "MONTHLY").upper(),
            features=features,
            trial_days=int(data.get("trial_days", 0)),
            is_active=data.get("is_active", True),
            price_display_mode=validate_price_display_mode(
                data.get("price_display_mode")
            ),
        )

        if "tax_ids" in data:
            plan.taxes = _resolve_active_taxes(data["tax_ids"])

        plan_repo = TarifPlanRepository(db.session)
        saved_plan = plan_repo.save(plan)
        invalidate_plan_cache()

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
        plan.price = float(data["price"])
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
    if "price_display_mode" in data:
        try:
            plan.price_display_mode = validate_price_display_mode(
                data["price_display_mode"]
            )
        except ValueError as mode_error:
            return jsonify({"error": str(mode_error)}), 400
    if "tax_ids" in data:
        # Replace-set: the new assignment fully supersedes the old one.
        try:
            plan.taxes = _resolve_active_taxes(data["tax_ids"])
        except TaxAssignmentError as tax_error:
            return jsonify({"error": str(tax_error)}), 400

    saved_plan = plan_repo.save(plan)
    invalidate_plan_cache()

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
    invalidate_plan_cache()

    return jsonify({"message": "Plan deleted successfully"}), 200


@subscription_bp.route(
    "/api/v1/admin/tarif-plans/<plan_id>/deactivate", methods=["POST"]
)
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
    invalidate_plan_cache()

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
    invalidate_plan_cache()

    return jsonify({"plan": saved_plan.to_dict(), "message": "Plan activated"}), 200


@subscription_bp.route("/api/v1/admin/tarif-plans/<plan_id>/archive", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_archive_plan(plan_id):
    """Archive (deactivate) a tariff plan. Alias for /deactivate for backwards compat."""
    return admin_deactivate_plan(plan_id)


@subscription_bp.route("/api/v1/admin/tarif-plans/<plan_id>/copy", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_copy_plan(plan_id):
    """
    Create a copy of an existing tariff plan.

    The copy is always inactive, re-points the source's tax / category / add-on
    links, and never carries the source's user subscriptions. See
    ``TarifPlanService.copy_plan`` for the full contract.

    Args:
        plan_id: UUID of the source plan

    Returns:
        201: New plan created
        404: Source plan not found
    """
    service = TarifPlanService(TarifPlanRepository(db.session))
    new_plan = service.copy_plan(plan_id)

    if new_plan is None:
        return jsonify({"error": "Plan not found"}), 404

    invalidate_plan_cache()

    return (
        jsonify({"plan": new_plan.to_dict(), "message": "Plan copied successfully"}),
        201,
    )


@subscription_bp.route("/api/v1/admin/tarif-plans/bulk/copy", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_bulk_copy_plans():
    """
    Copy several tariff plans in one request.

    Body:
        - ids: list[str] — source plan UUIDs. Unknown ids are skipped, not fatal.

    Each created copy follows the same contract as the per-item ``/copy`` route.

    Returns:
        201: ``{"plans": [...], "count": N}`` — every created copy.
    """
    data = request.get_json() or {}
    plan_ids = data.get("ids", []) or []

    service = TarifPlanService(TarifPlanRepository(db.session))
    created_plans = []
    for plan_id in plan_ids:
        new_plan = service.copy_plan(plan_id)
        if new_plan is not None:
            created_plans.append(new_plan.to_dict())

    invalidate_plan_cache()

    return (
        jsonify(
            {
                "plans": created_plans,
                "count": len(created_plans),
                "message": "Plans copied successfully",
            }
        ),
        201,
    )
