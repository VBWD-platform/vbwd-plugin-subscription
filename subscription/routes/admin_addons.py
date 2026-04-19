"""Admin add-on management routes."""
import re
from flask import jsonify, request
from decimal import Decimal
from vbwd.middleware.auth import require_auth, require_admin, require_permission
from plugins.subscription.subscription.repositories.addon_repository import AddOnRepository
from vbwd.extensions import db
from plugins.subscription.subscription.models import AddOn
from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.routes import subscription_bp


def generate_slug(name: str) -> str:
    """Generate URL-safe slug from name."""
    # Convert to lowercase
    slug = name.lower()
    # Replace spaces and underscores with hyphens
    slug = re.sub(r"[\s_]+", "-", slug)
    # Remove any characters that aren't alphanumeric or hyphens
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    # Remove multiple consecutive hyphens
    slug = re.sub(r"-+", "-", slug)
    # Strip leading/trailing hyphens
    slug = slug.strip("-")
    return slug


@subscription_bp.route("/api/v1/admin/addons/", methods=["GET"])
@require_auth
@require_admin
@require_permission("subscription.addons.manage")
def admin_list_addons():
    """
    List all add-ons with pagination.

    Query params:
        - page: int (default 1)
        - per_page: int (default 20)
        - include_inactive: bool (default true)

    Returns:
        200: Paginated list of add-ons
    """
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    include_inactive = request.args.get("include_inactive", "true").lower() == "true"

    # Limit per_page to reasonable values
    per_page = min(max(per_page, 1), 100)

    addon_repo = AddOnRepository(db.session)
    addons, total = addon_repo.find_all_paginated(
        page=page,
        per_page=per_page,
        include_inactive=include_inactive,
    )

    return (
        jsonify(
            {
                "items": [addon.to_dict() for addon in addons],
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": (total + per_page - 1) // per_page if total > 0 else 0,
            }
        ),
        200,
    )


@subscription_bp.route("/api/v1/admin/addons/", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.addons.manage")
def admin_create_addon():
    """
    Create a new add-on.

    Body:
        - name: str (required)
        - slug: str (optional, auto-generated from name)
        - description: str (optional)
        - price: decimal (required)
        - currency: str (default: EUR)
        - billing_period: str (default: monthly)
        - config: dict (optional, default: {})
        - is_active: bool (default: true)
        - sort_order: int (default: 0)

    Returns:
        201: Created add-on
        400: Validation error
    """
    data = request.get_json() or {}

    # Validate required fields
    if not data.get("name"):
        return jsonify({"error": "Name is required"}), 400
    if "price" not in data:
        return jsonify({"error": "Price is required"}), 400

    try:
        price = Decimal(str(data["price"]))
        if price < 0:
            return jsonify({"error": "Price cannot be negative"}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid price"}), 400

    # Generate slug if not provided
    slug = data.get("slug") or generate_slug(data["name"])

    # Check slug uniqueness
    addon_repo = AddOnRepository(db.session)
    if addon_repo.slug_exists(slug):
        return jsonify({"error": "Slug already exists"}), 400

    # Resolve tarif_plan_ids to TarifPlan objects
    tarif_plan_ids = data.get("tarif_plan_ids", [])
    tarif_plans = []
    if tarif_plan_ids:
        tarif_plans = (
            db.session.query(TarifPlan).filter(TarifPlan.id.in_(tarif_plan_ids)).all()
        )
        if len(tarif_plans) != len(tarif_plan_ids):
            return jsonify({"error": "One or more tariff plan IDs are invalid"}), 400

    try:
        addon = AddOn(
            name=data["name"],
            slug=slug,
            description=data.get("description"),
            price=price,
            currency=data.get("currency", "EUR"),
            billing_period=data.get("billing_period", "monthly"),
            config=data.get("config", {}),
            is_active=data.get("is_active", True),
            sort_order=data.get("sort_order", 0),
        )
        addon.tarif_plans = tarif_plans

        saved_addon = addon_repo.save(addon)

        return (
            jsonify(
                {
                    "addon": saved_addon.to_dict(),
                    "message": "Add-on created successfully",
                }
            ),
            201,
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 400


@subscription_bp.route("/api/v1/admin/addons/<addon_id>", methods=["GET"])
@require_auth
@require_admin
@require_permission("subscription.addons.manage")
def admin_get_addon(addon_id):
    """
    Get add-on details.

    Args:
        addon_id: UUID of the add-on

    Returns:
        200: Add-on details
        404: Add-on not found
    """
    addon_repo = AddOnRepository(db.session)
    addon = addon_repo.find_by_id(addon_id)

    if not addon:
        return jsonify({"error": "Add-on not found"}), 404

    return jsonify({"addon": addon.to_dict()}), 200


@subscription_bp.route("/api/v1/admin/addons/slug/<slug>", methods=["GET"])
@require_auth
@require_admin
@require_permission("subscription.addons.manage")
def admin_get_addon_by_slug(slug):
    """
    Get add-on details by slug.

    Args:
        slug: Slug of the add-on

    Returns:
        200: Add-on details
        404: Add-on not found
    """
    addon_repo = AddOnRepository(db.session)
    addon = addon_repo.find_by_slug(slug)

    if not addon:
        return jsonify({"error": "Add-on not found"}), 404

    return jsonify({"addon": addon.to_dict()}), 200


@subscription_bp.route("/api/v1/admin/addons/<addon_id>", methods=["PUT"])
@require_auth
@require_admin
@require_permission("subscription.addons.manage")
def admin_update_addon(addon_id):
    """
    Update add-on details.

    Args:
        addon_id: UUID of the add-on

    Body:
        - name: str (optional)
        - slug: str (optional)
        - description: str (optional)
        - price: decimal (optional)
        - currency: str (optional)
        - billing_period: str (optional)
        - config: dict (optional)
        - is_active: bool (optional)
        - sort_order: int (optional)

    Returns:
        200: Updated add-on
        404: Add-on not found
        400: Validation error
    """
    addon_repo = AddOnRepository(db.session)
    addon = addon_repo.find_by_id(addon_id)

    if not addon:
        return jsonify({"error": "Add-on not found"}), 404

    data = request.get_json() or {}

    if "name" in data:
        if not data["name"]:
            return jsonify({"error": "Name cannot be empty"}), 400
        addon.name = data["name"]

    if "slug" in data:
        if addon_repo.slug_exists(data["slug"], exclude_id=addon.id):
            return jsonify({"error": "Slug already exists"}), 400
        addon.slug = data["slug"]

    if "description" in data:
        addon.description = data["description"]

    if "price" in data:
        try:
            price = Decimal(str(data["price"]))
            if price < 0:
                return jsonify({"error": "Price cannot be negative"}), 400
            addon.price = price
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid price"}), 400

    if "currency" in data:
        addon.currency = data["currency"]

    if "billing_period" in data:
        addon.billing_period = data["billing_period"]

    if "config" in data:
        addon.config = data["config"]

    if "is_active" in data:
        addon.is_active = bool(data["is_active"])

    if "sort_order" in data:
        addon.sort_order = int(data.get("sort_order", 0))

    if "tarif_plan_ids" in data:
        tarif_plan_ids = data["tarif_plan_ids"]
        if tarif_plan_ids:
            tarif_plans = (
                db.session.query(TarifPlan)
                .filter(TarifPlan.id.in_(tarif_plan_ids))
                .all()
            )
            if len(tarif_plans) != len(tarif_plan_ids):
                return (
                    jsonify({"error": "One or more tariff plan IDs are invalid"}),
                    400,
                )
            addon.tarif_plans = tarif_plans
        else:
            addon.tarif_plans = []

    saved_addon = addon_repo.save(addon)

    return jsonify({"addon": saved_addon.to_dict()}), 200


@subscription_bp.route("/api/v1/admin/addons/<addon_id>", methods=["DELETE"])
@require_auth
@require_admin
@require_permission("subscription.addons.manage")
def admin_delete_addon(addon_id):
    """
    Delete an add-on.

    Args:
        addon_id: UUID of the add-on

    Returns:
        200: Add-on deleted
        404: Add-on not found
    """
    addon_repo = AddOnRepository(db.session)
    addon = addon_repo.find_by_id(addon_id)

    if not addon:
        return jsonify({"error": "Add-on not found"}), 404

    addon_repo.delete(addon_id)

    return jsonify({"message": "Add-on deleted successfully"}), 200


@subscription_bp.route("/api/v1/admin/addons/<addon_id>/activate", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.addons.manage")
def admin_activate_addon(addon_id):
    """
    Activate an add-on.

    Args:
        addon_id: UUID of the add-on

    Returns:
        200: Add-on activated
        404: Add-on not found
    """
    addon_repo = AddOnRepository(db.session)
    addon = addon_repo.find_by_id(addon_id)

    if not addon:
        return jsonify({"error": "Add-on not found"}), 404

    addon.is_active = True
    saved_addon = addon_repo.save(addon)

    return jsonify({"addon": saved_addon.to_dict(), "message": "Add-on activated"}), 200


@subscription_bp.route("/api/v1/admin/addons/<addon_id>/deactivate", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.addons.manage")
def admin_deactivate_addon(addon_id):
    """
    Deactivate an add-on.

    Args:
        addon_id: UUID of the add-on

    Returns:
        200: Add-on deactivated
        404: Add-on not found
    """
    addon_repo = AddOnRepository(db.session)
    addon = addon_repo.find_by_id(addon_id)

    if not addon:
        return jsonify({"error": "Add-on not found"}), 404

    addon.is_active = False
    saved_addon = addon_repo.save(addon)

    return (
        jsonify({"addon": saved_addon.to_dict(), "message": "Add-on deactivated"}),
        200,
    )
