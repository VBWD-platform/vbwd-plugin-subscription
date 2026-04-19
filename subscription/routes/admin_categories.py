"""Admin tariff plan category management routes."""
from flask import jsonify, request
from vbwd.middleware.auth import require_auth, require_admin, require_permission
from plugins.subscription.subscription.repositories.tarif_plan_category_repository import (
    TarifPlanCategoryRepository,
)
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)
from plugins.subscription.subscription.services.tarif_plan_category_service import (
    TarifPlanCategoryService,
)
from vbwd.extensions import db
from plugins.subscription.subscription.routes import subscription_bp

PREFIX = "/api/v1/admin/tarif-plan-categories"


def _get_service() -> TarifPlanCategoryService:
    return TarifPlanCategoryService(
        category_repo=TarifPlanCategoryRepository(db.session),
        tarif_plan_repo=TarifPlanRepository(db.session),
    )


@subscription_bp.route(f"{PREFIX}/", methods=["GET"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_list_categories():
    service = _get_service()
    fmt = request.args.get("format", "flat")
    if fmt == "tree":
        categories = service.get_tree()
    else:
        categories = service.get_all()
    return jsonify({"categories": [c.to_dict() for c in categories]}), 200


@subscription_bp.route(f"{PREFIX}/", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_create_category():
    data = request.get_json() or {}
    if not data.get("name"):
        return jsonify({"error": "Name is required"}), 400
    try:
        service = _get_service()
        category = service.create(
            name=data["name"],
            slug=data.get("slug"),
            description=data.get("description"),
            parent_id=data.get("parent_id"),
            is_single=data.get("is_single", True),
            sort_order=int(data.get("sort_order", 0)),
        )
        return jsonify({"category": category.to_dict(), "message": "Category created successfully"}), 201
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@subscription_bp.route(f"{PREFIX}/<category_id>", methods=["GET"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_get_category(category_id):
    service = _get_service()
    category = service.get_by_id(category_id)
    if not category:
        return jsonify({"error": "Category not found"}), 404
    return jsonify({"category": category.to_dict()}), 200


@subscription_bp.route(f"{PREFIX}/<category_id>", methods=["PUT"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_update_category(category_id):
    data = request.get_json() or {}
    try:
        service = _get_service()
        category = service.update(category_id, **data)
        return jsonify({"category": category.to_dict()}), 200
    except ValueError as error:
        message = str(error)
        if "not found" in message.lower():
            return jsonify({"error": message}), 404
        return jsonify({"error": message}), 400


@subscription_bp.route(f"{PREFIX}/<category_id>", methods=["DELETE"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_delete_category(category_id):
    try:
        service = _get_service()
        service.delete(category_id)
        return jsonify({"message": "Category deleted successfully"}), 200
    except ValueError as error:
        message = str(error)
        if "not found" in message.lower():
            return jsonify({"error": message}), 404
        return jsonify({"error": message}), 400


@subscription_bp.route(f"{PREFIX}/<category_id>/attach-plans", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_attach_plans(category_id):
    data = request.get_json() or {}
    plan_ids = data.get("plan_ids", [])
    if not plan_ids:
        return jsonify({"error": "plan_ids is required"}), 400
    try:
        service = _get_service()
        category = service.attach_plans(category_id, plan_ids)
        return jsonify({"category": category.to_dict()}), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@subscription_bp.route(f"{PREFIX}/<category_id>/detach-plans", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.plans.manage")
def admin_detach_plans(category_id):
    data = request.get_json() or {}
    plan_ids = data.get("plan_ids", [])
    if not plan_ids:
        return jsonify({"error": "plan_ids is required"}), 400
    try:
        service = _get_service()
        category = service.detach_plans(category_id, plan_ids)
        return jsonify({"category": category.to_dict()}), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
