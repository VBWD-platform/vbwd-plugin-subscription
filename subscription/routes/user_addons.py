"""Public add-on routes (for user checkout and catalog)."""
from flask import jsonify, g, current_app
from vbwd.middleware.auth import optional_auth
from vbwd.pricing.display_mode import display_mode_fields
from vbwd.pricing.price_payload import build_pricing_block
from plugins.subscription.subscription.repositories.addon_repository import (
    AddOnRepository,
)
from vbwd.extensions import db
from plugins.subscription.subscription.models import Subscription
from vbwd.models.enums import SubscriptionStatus
from vbwd.services.cache import cached_response, resolve_cache_store
from plugins.subscription.subscription.cache_keys import (
    addon_list_cache_key,
    catalog_cache_ttl_seconds,
)
from plugins.subscription.subscription.routes import subscription_bp


def _addon_dict_with_price(addon) -> dict:
    """Serialise an add-on with the computed ``Price`` block (S85.2).

    Routes the price math through the single core ``PriceFactory`` (D1) and
    embeds the serialized ``Price`` (``{netto, taxes, brutto, currency}``)
    alongside the existing fields — backward-compatible (the bare ``price``
    number stays). The display-mode pair (``effective_display_mode`` /
    ``prices_display_mode``) is added so the add-on surfaces can pick the
    net/gross side + the business overlay (S85.4).
    """
    addon_dict = addon.to_dict()
    price = current_app.container.price_factory().get_price_from_object(addon)
    addon_dict["price_info"] = build_pricing_block(price)
    addon_dict.update(display_mode_fields(addon))
    return addon_dict


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
    # Per-user (authenticated) results are NEVER cached: an authenticated user
    # may see plan-bound add-ons specific to their subscription. Only the public
    # path (no user → independent add-ons only) is cacheable this sprint.
    if hasattr(g, "user_id"):
        addon_repo = AddOnRepository(db.session)
        subscription = (
            db.session.query(Subscription)
            .filter(
                Subscription.user_id == g.user_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
            .first()
        )
        plan_id = subscription.tarif_plan_id if subscription else None
        addons = addon_repo.find_available_for_plan(plan_id)
        return (
            jsonify({"addons": [_addon_dict_with_price(addon) for addon in addons]}),
            200,
        )

    def produce_public_addons():
        addon_repo = AddOnRepository(db.session)
        addons = addon_repo.find_available_for_plan(None)
        return {"addons": [_addon_dict_with_price(addon) for addon in addons]}, 200

    body, status = cached_response(
        resolve_cache_store(),
        addon_list_cache_key(),
        catalog_cache_ttl_seconds(),
        produce_public_addons,
    )
    return jsonify(body), status


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

    addon_dict = _addon_dict_with_price(addon)

    # S77 — append the generic tags / custom fields (opt-in, no model import).
    # The fe-user add-on card reads these keys + the field defs (labels + types)
    # off the payload without an extra round trip.
    from vbwd.services.tags_and_custom_fields import (
        append_tags_and_custom_fields,
        resolve_tags_and_custom_fields,
    )

    append_tags_and_custom_fields(addon_dict, "addon", addon.id)
    addon_dict["custom_field_defs"] = resolve_tags_and_custom_fields().get_field_defs(
        "addon"
    )

    return jsonify({"addon": addon_dict}), 200
