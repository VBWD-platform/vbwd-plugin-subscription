"""User-scoped checkout + add-on subscription routes.

Relocated verbatim from core `vbwd/routes/user.py` (Sprint 03/S2). Same
absolute URLs, same behaviour — now owned by the subscription plugin so
core carries no checkout/subscription route.
"""
from uuid import UUID

from flask import request, jsonify, g, current_app

from vbwd.middleware.auth import require_auth
from vbwd.extensions import db

from plugins.subscription.subscription.events import CheckoutRequestedEvent

from plugins.subscription.subscription.routes import subscription_bp


@subscription_bp.route("/api/v1/user/checkout", methods=["POST"])
@require_auth
def checkout():
    """Create checkout with subscription and optional items.

    Requires: Bearer token in Authorization header

    Request body:
        {
            "plan_id": "uuid-here",
            "token_bundle_ids": ["uuid-1", "uuid-2"],  # optional
            "add_on_ids": ["uuid-1", "uuid-2"],        # optional
            "currency": "USD"                           # optional, defaults to USD
        }

    Returns:
        200: {
            "subscription": {...},
            "invoice": {...},
            "token_bundles": [...],
            "add_ons": [...],
            "message": "Checkout created. Awaiting payment."
        }
        400: If validation fails
        404: If plan/bundle/addon not found
    """
    user_id = g.user_id
    data = request.get_json() or {}

    # Validate: at least one item required
    plan_id = data.get("plan_id")
    token_bundle_ids_raw = data.get("token_bundle_ids", [])
    add_on_ids_raw = data.get("add_on_ids", [])

    if not plan_id and not token_bundle_ids_raw and not add_on_ids_raw:
        return (
            jsonify(
                {
                    "error": "At least one item required (plan_id, token_bundle_ids, or add_on_ids)"
                }
            ),
            400,
        )

    # Parse plan UUID (optional now)
    plan_uuid = None
    if plan_id:
        try:
            plan_uuid = UUID(plan_id) if isinstance(plan_id, str) else plan_id
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid plan_id format"}), 400

    # Parse optional token bundle IDs
    token_bundle_ids = []
    for bundle_id in token_bundle_ids_raw:
        try:
            token_bundle_ids.append(
                UUID(bundle_id) if isinstance(bundle_id, str) else bundle_id
            )
        except (ValueError, TypeError):
            return jsonify({"error": f"Invalid token_bundle_id: {bundle_id}"}), 400

    # Parse optional add-on IDs
    add_on_ids = []
    for addon_id in add_on_ids_raw:
        try:
            add_on_ids.append(UUID(addon_id) if isinstance(addon_id, str) else addon_id)
        except (ValueError, TypeError):
            return jsonify({"error": f"Invalid add_on_id: {addon_id}"}), 400

    # Get currency and payment method
    currency = data.get("currency", "USD")
    payment_method_code = data.get("payment_method_code")
    coupon_code = data.get("coupon_code")

    # Create checkout event
    event = CheckoutRequestedEvent(
        user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
        plan_id=plan_uuid,
        token_bundle_ids=token_bundle_ids,
        add_on_ids=add_on_ids,
        currency=currency,
        payment_method_code=payment_method_code,
        coupon_code=coupon_code,
    )

    # Dispatch event
    container = current_app.container
    dispatcher = container.event_dispatcher()
    result = dispatcher.emit(event)

    if result.success:
        # Unwrap single-item list from EventResult.combine()
        data = result.data
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        return jsonify(data), 201
    else:
        # Map error types to HTTP status codes
        # Return 400 for validation errors (not found, not active, etc.)
        # Return 500 only for system errors
        status_code = 400
        if result.error_type == "no_handler":
            status_code = 500

        return jsonify({"error": result.error}), status_code


@subscription_bp.route("/api/v1/user/addons", methods=["GET"])
@require_auth
def get_user_addons():
    """Get current user's add-on subscriptions with addon details.

    Requires: Bearer token in Authorization header

    Returns:
        200: {"addon_subscriptions": [...]}
    """
    user_id = g.user_id
    container = current_app.container

    addon_sub_repo = container.addon_subscription_repository()

    addon_subs = addon_sub_repo.find_by_user(
        UUID(user_id) if isinstance(user_id, str) else user_id
    )

    result = []
    for addon_sub in addon_subs:
        data = addon_sub.to_dict()
        # Addon details are eager-loaded via relationship
        if addon_sub.addon:
            data["addon"] = {
                "name": addon_sub.addon.name,
                "slug": addon_sub.addon.slug,
                "description": addon_sub.addon.description,
                "price": str(addon_sub.addon.price) if addon_sub.addon.price else None,
                "billing_period": addon_sub.addon.billing_period
                if addon_sub.addon.billing_period
                else None,
            }
        result.append(data)

    return jsonify({"addon_subscriptions": result}), 200


@subscription_bp.route("/api/v1/user/addons/<addon_sub_id>", methods=["GET"])
@require_auth
def get_addon_detail(addon_sub_id):
    """Get addon subscription detail with addon and invoice info.

    Requires: Bearer token in Authorization header

    Args:
        addon_sub_id: UUID of the addon subscription

    Returns:
        200: Addon subscription with addon and invoice details
        403: Access denied (not owner)
        404: Addon subscription not found
    """
    user_id = g.user_id
    container = current_app.container

    addon_sub_repo = container.addon_subscription_repository()
    addon_sub = addon_sub_repo.find_by_id(addon_sub_id)

    if not addon_sub:
        return jsonify({"error": "Add-on subscription not found"}), 404

    if str(addon_sub.user_id) != str(user_id):
        return jsonify({"error": "Access denied"}), 403

    data = addon_sub.to_dict()

    # Add addon details
    if addon_sub.addon:
        data["addon"] = {
            "name": addon_sub.addon.name,
            "slug": addon_sub.addon.slug,
            "description": addon_sub.addon.description,
            "price": str(addon_sub.addon.price) if addon_sub.addon.price else None,
            "billing_period": addon_sub.addon.billing_period
            if addon_sub.addon.billing_period
            else None,
        }

    # Add invoice details
    if addon_sub.invoice_id:
        invoice_repo = container.invoice_repository()
        invoice = invoice_repo.find_by_id(addon_sub.invoice_id)
        if invoice:
            data["invoice"] = {
                "id": str(invoice.id),
                "invoice_number": invoice.invoice_number,
                "status": invoice.status.value,
                "amount": str(invoice.amount),
                "currency": invoice.currency,
            }

    return jsonify({"addon_subscription": data}), 200


@subscription_bp.route("/api/v1/user/addons/<addon_sub_id>/cancel", methods=["POST"])
@require_auth
def cancel_addon(addon_sub_id):
    """Cancel an addon subscription.

    Requires: Bearer token in Authorization header

    Args:
        addon_sub_id: UUID of the addon subscription

    Returns:
        200: Updated addon subscription
        400: Cannot cancel (already cancelled/expired)
        403: Access denied (not owner)
        404: Addon subscription not found
    """
    from vbwd.models.enums import SubscriptionStatus

    user_id = g.user_id
    container = current_app.container

    addon_sub_repo = container.addon_subscription_repository()
    addon_sub = addon_sub_repo.find_by_id(addon_sub_id)

    if not addon_sub:
        return jsonify({"error": "Add-on subscription not found"}), 404

    if str(addon_sub.user_id) != str(user_id):
        return jsonify({"error": "Access denied"}), 403

    if addon_sub.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PENDING):
        return jsonify({"error": "Add-on subscription cannot be cancelled"}), 400

    addon_sub.cancel()
    db.session.commit()

    return (
        jsonify(
            {
                "addon_subscription": addon_sub.to_dict(),
                "message": "Add-on cancelled successfully",
            }
        ),
        200,
    )
