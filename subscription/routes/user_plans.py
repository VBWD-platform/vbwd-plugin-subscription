"""Tariff plan routes."""
import uuid
from flask import request, jsonify, current_app
from vbwd.extensions import db
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)
from plugins.subscription.subscription.repositories.tarif_plan_category_repository import (
    TarifPlanCategoryRepository,
)
from vbwd.repositories.currency_repository import CurrencyRepository
from vbwd.repositories.tax_repository import TaxRepository
from plugins.subscription.subscription.services.tarif_plan_service import (
    TarifPlanService,
)
from vbwd.services.currency_service import CurrencyService
from vbwd.services.core_settings_store import (
    get_core_settings,
    update_core_settings,
)
from vbwd.services.tax_service import TaxService
from vbwd.services.cache import cached_response, resolve_cache_store
from plugins.subscription.subscription.cache_keys import (
    catalog_cache_ttl_seconds,
    plan_detail_cache_key,
    plan_list_cache_key,
)
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

    def produce_plan_list():
        # Initialize services
        plan_repo = TarifPlanRepository(db.session)
        currency_repo = CurrencyRepository(db.session)  # type: ignore[arg-type]
        tax_repo = TaxRepository(db.session)  # type: ignore[arg-type]

        currency_service = CurrencyService(
            currency_repo=currency_repo,
            settings_reader=get_core_settings,
            settings_writer=update_core_settings,
        )
        tax_service = TaxService(tax_repo=tax_repo)
        tarif_plan_service = TarifPlanService(
            tarif_plan_repo=plan_repo,
            currency_service=currency_service,
            tax_service=tax_service,
            price_factory=current_app.container.price_factory(),
        )

        # Get active plans, optionally filtered by category
        if category_slug:
            category_repo = TarifPlanCategoryRepository(db.session)
            category = category_repo.find_by_slug(category_slug)
            if not category:
                return {"error": f"Category '{category_slug}' not found"}, 404
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
                    "price": plan.raw_price,
                    "billing_period": plan.billing_period.value,
                    "is_active": plan.is_active,
                    "error": str(e),
                }
                result.append(plan_data)

        return (
            {
                "plans": result,
                "currency": currency_code,
                "country": country_code,
            },
            200,
        )

    # Cache the resolved public list per (currency, country, category); only
    # 2xx bodies are cached. Admin plan writes clear the ``tarif-plans:`` prefix.
    cache_key = plan_list_cache_key(currency_code, country_code, category_slug)
    body, status = cached_response(
        resolve_cache_store(),
        cache_key,
        catalog_cache_ttl_seconds(),
        produce_plan_list,
    )
    return jsonify(body), status


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

    def produce_plan_detail():
        # Initialize services
        plan_repo = TarifPlanRepository(db.session)
        currency_repo = CurrencyRepository(db.session)  # type: ignore[arg-type]
        tax_repo = TaxRepository(db.session)  # type: ignore[arg-type]

        currency_service = CurrencyService(
            currency_repo=currency_repo,
            settings_reader=get_core_settings,
            settings_writer=update_core_settings,
        )
        tax_service = TaxService(tax_repo=tax_repo)
        tarif_plan_service = TarifPlanService(
            tarif_plan_repo=plan_repo,
            currency_service=currency_service,
            tax_service=tax_service,
            price_factory=current_app.container.price_factory(),
        )

        # Get plan by UUID or slug
        plan = None
        try:
            uuid.UUID(slug_or_id)
            plan = plan_repo.find_by_id(slug_or_id)
        except ValueError:
            plan = tarif_plan_service.get_plan_by_slug(slug_or_id)

        if not plan:
            return {"error": "Plan not found"}, 404

        # Add pricing info, merge with full plan data. When pricing resolution
        # fails (e.g. the requested currency / FX rate is not seeded) we degrade
        # gracefully to 200 with the base price — exactly like ``list_plans`` —
        # so a missing rate never 400s a valid plan lookup. The plan was already
        # resolved above, so this is never a "not found" case.
        plan_data = plan.to_dict()
        try:
            pricing = tarif_plan_service.get_plan_with_pricing(
                plan,
                currency_code=currency_code,
                country_code=country_code,
            )
            plan_data.update(pricing)
        except ValueError as pricing_error:
            plan_data["pricing_error"] = str(pricing_error)

        # S77 — append the generic tags / custom fields (opt-in, no model
        # import). The fe-user tarif card reads these keys + the field defs
        # (labels + types) off the payload without an extra round trip.
        from vbwd.services.tags_and_custom_fields import (
            append_tags_and_custom_fields,
            resolve_tags_and_custom_fields,
        )

        append_tags_and_custom_fields(plan_data, "tarif_plan", plan.id)
        plan_data[
            "custom_field_defs"
        ] = resolve_tags_and_custom_fields().get_field_defs("tarif_plan")
        return plan_data, 200

    # Cache 2xx only, keyed by (slug-or-id, currency, country). A 404 is never
    # cached; admin plan writes clear the ``tarif-plans:`` prefix.
    cache_key = plan_detail_cache_key(slug_or_id, currency_code, country_code)
    body, status = cached_response(
        resolve_cache_store(),
        cache_key,
        catalog_cache_ttl_seconds(),
        produce_plan_detail,
    )
    return jsonify(body), status
