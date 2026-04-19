"""Tariff plan routes."""
import uuid
from flask import request, jsonify
from vbwd.extensions import db
from plugins.subscription.subscription.repositories.tarif_plan_repository import TarifPlanRepository
from plugins.subscription.subscription.repositories.tarif_plan_category_repository import TarifPlanCategoryRepository
from vbwd.repositories.currency_repository import CurrencyRepository
from vbwd.repositories.tax_repository import TaxRepository
from plugins.subscription.subscription.services.tarif_plan_service import TarifPlanService
from vbwd.services.currency_service import CurrencyService
from vbwd.services.tax_service import TaxService
from plugins.subscription.subscription.routes import subscription_bp


@subscription_bp.route("/api/v1/tarif-plans", methods=["GET"])
def list_plans():
    """
    List active tariff plans.

    GET /api/v1/tarif-plans?currency=USD&country=DE&category=root

    Query params:
        currency: Currency code for pricing (default: EUR)
        country: Country code for tax calculation (optional)
        category: Category slug to filter plans (optional)

    Returns:
        200: List of plans with pricing in specified currency
    """
    currency_code = request.args.get("currency", "EUR").upper()
    country_code = request.args.get("country", "").upper() or None
    category_slug = request.args.get("category")

    # Initialize services
    plan_repo = TarifPlanRepository(db.session)
    currency_repo = CurrencyRepository(db.session)  # type: ignore[arg-type]
    tax_repo = TaxRepository(db.session)  # type: ignore[arg-type]

    currency_service = CurrencyService(currency_repo=currency_repo)
    tax_service = TaxService(tax_repo=tax_repo)
    tarif_plan_service = TarifPlanService(
        tarif_plan_repo=plan_repo,
        currency_service=currency_service,
        tax_service=tax_service,
    )

    # Get active plans, optionally filtered by category
    if category_slug:
        category_repo = TarifPlanCategoryRepository(db.session)
        category = category_repo.find_by_slug(category_slug)
        if not category:
            return jsonify({"error": f"Category '{category_slug}' not found"}), 404
        plans = [p for p in category.tarif_plans if p.is_active]
    else:
        plans = tarif_plan_service.get_active_plans()

    # Add pricing info to each plan
    result = []
    for plan in plans:
        try:
            plan_data = tarif_plan_service.get_plan_with_pricing(
                plan,
                currency_code=currency_code,
                country_code=country_code,
            )
            result.append(plan_data)
        except ValueError as e:
            # Currency not found - use default
            plan_data = {
                "id": str(plan.id),
                "name": plan.name,
                "slug": plan.slug,
                "description": plan.description,
                "price": plan.price_float,
                "billing_period": plan.billing_period.value,
                "is_active": plan.is_active,
                "error": str(e),
            }
            result.append(plan_data)

    return (
        jsonify(
            {
                "plans": result,
                "currency": currency_code,
                "country": country_code,
            }
        ),
        200,
    )


@subscription_bp.route("/api/v1/tarif-plans/<slug_or_id>", methods=["GET"])
def get_plan(slug_or_id: str):
    """
    Get single tariff plan by slug or UUID.

    GET /api/v1/tarif-plans/pro?currency=USD&country=DE
    GET /api/v1/tarif-plans/<uuid>?currency=USD&country=DE

    Path params:
        slug_or_id: Plan URL slug or UUID

    Query params:
        currency: Currency code for pricing (default: EUR)
        country: Country code for tax calculation (optional)

    Returns:
        200: Plan details with pricing
        404: Plan not found
    """
    currency_code = request.args.get("currency", "EUR").upper()
    country_code = request.args.get("country", "").upper() or None

    # Initialize services
    plan_repo = TarifPlanRepository(db.session)
    currency_repo = CurrencyRepository(db.session)  # type: ignore[arg-type]
    tax_repo = TaxRepository(db.session)  # type: ignore[arg-type]

    currency_service = CurrencyService(currency_repo=currency_repo)
    tax_service = TaxService(tax_repo=tax_repo)
    tarif_plan_service = TarifPlanService(
        tarif_plan_repo=plan_repo,
        currency_service=currency_service,
        tax_service=tax_service,
    )

    # Get plan by UUID or slug
    plan = None
    try:
        uuid.UUID(slug_or_id)
        plan = plan_repo.find_by_id(slug_or_id)
    except ValueError:
        plan = tarif_plan_service.get_plan_by_slug(slug_or_id)

    if not plan:
        return jsonify({"error": "Plan not found"}), 404

    # Add pricing info, merge with full plan data
    try:
        plan_data = plan.to_dict()
        pricing = tarif_plan_service.get_plan_with_pricing(
            plan,
            currency_code=currency_code,
            country_code=country_code,
        )
        plan_data.update(pricing)
        return jsonify(plan_data), 200
    except ValueError as e:
        return (
            jsonify(
                {
                    "error": str(e),
                    "plan": {
                        "id": str(plan.id),
                        "name": plan.name,
                        "slug": plan.slug,
                        "price": plan.price_float,
                    },
                }
            ),
            400,
        )
