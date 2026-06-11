"""Public bot checkout-draft resolution endpoint (S53.0 / D8).

``GET /api/v1/subscription/public/checkout-draft/<token>`` — NO auth.

Resolves a one-time bot checkout-draft token to line items whose names/prices
are **recomputed from the live catalogs** server-side. The draft persists only
``{item_type, item_id, quantity}`` — never a price — so the URL carries no
identity and no amount. The endpoint creates NO invoice/subscription/charge;
payment still happens through the normal browser checkout. The token is
single-use + expiring: an expired or already-redeemed token returns 404.
"""
from uuid import UUID

from flask import jsonify

from vbwd.extensions import db
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)
from plugins.subscription.subscription.repositories.addon_repository import (
    AddOnRepository,
)
from plugins.subscription.subscription.repositories.bot_checkout_draft_repository import (  # noqa: E501
    BotCheckoutDraftRepository,
)
from plugins.subscription.subscription.services.bot_storefront_service import (
    BotStorefrontService,
    DraftResolutionError,
)
from vbwd.repositories.token_bundle_repository import TokenBundleRepository
from plugins.subscription.subscription.routes import subscription_bp


def _as_uuid(raw_id: str):
    """Best-effort UUID coercion for a catalog id (returns None when invalid)."""
    try:
        return UUID(str(raw_id))
    except (ValueError, TypeError):
        return None


def _build_storefront_service() -> BotStorefrontService:
    from flask import current_app

    config = current_app.config_store.get_config("subscription")
    ttl_seconds = config.get("checkout_draft_ttl_seconds", 900)
    return BotStorefrontService(
        BotCheckoutDraftRepository(db.session),
        checkout_draft_ttl_seconds=ttl_seconds,
    )


@subscription_bp.route(
    "/api/v1/subscription/public/checkout-draft/<token>", methods=["GET"]
)
def resolve_checkout_draft(token: str):
    """Resolve a bot checkout-draft token → recomputed line items (no auth)."""
    plan_repo = TarifPlanRepository(db.session)
    addon_repo = AddOnRepository(db.session)
    bundle_repo = TokenBundleRepository(db.session)

    def plan_lookup(item_id: str):
        item_uuid = _as_uuid(item_id)
        return plan_repo.find_by_id(item_uuid) if item_uuid else None

    def addon_lookup(item_id: str):
        item_uuid = _as_uuid(item_id)
        return addon_repo.find_by_id(item_uuid) if item_uuid else None

    def bundle_lookup(item_id: str):
        item_uuid = _as_uuid(item_id)
        return bundle_repo.find_by_id(item_uuid) if item_uuid else None

    service = _build_storefront_service()
    try:
        line_items = service.resolve_token(
            token,
            plan_lookup=plan_lookup,
            addon_lookup=addon_lookup,
            bundle_lookup=bundle_lookup,
        )
    except DraftResolutionError:
        # Single-use + expiring: unknown / expired / already-redeemed → 404.
        return jsonify({"error": "Checkout draft not found or expired"}), 404

    return jsonify({"line_items": line_items}), 200
