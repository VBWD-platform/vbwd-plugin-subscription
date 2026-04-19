"""Admin subscription management routes."""
from flask import jsonify, request
from datetime import datetime, timedelta
from vbwd.utils.datetime_utils import utcnow
from dateutil.relativedelta import relativedelta  # type: ignore[import-untyped]
from vbwd.middleware.auth import require_auth, require_admin, require_permission
from plugins.subscription.subscription.repositories.subscription_repository import SubscriptionRepository
from vbwd.repositories.user_repository import UserRepository
from plugins.subscription.subscription.repositories.tarif_plan_repository import TarifPlanRepository
from plugins.subscription.subscription.services.subscription_service import SubscriptionService
from vbwd.extensions import db
from plugins.subscription.subscription.models import Subscription
from vbwd.models.invoice import UserInvoice
from vbwd.models.enums import SubscriptionStatus, InvoiceStatus, BillingPeriod
from plugins.subscription.subscription.routes import subscription_bp


def _get_billing_months(billing_period: BillingPeriod) -> int:
    """Convert billing period to months."""
    period_map = {
        BillingPeriod.MONTHLY: 1,
        BillingPeriod.QUARTERLY: 3,
        BillingPeriod.YEARLY: 12,
        BillingPeriod.WEEKLY: 1,  # Treat as 1 month minimum
        BillingPeriod.ONE_TIME: 1200,  # ~100 years for lifetime
    }
    return period_map.get(billing_period, 1)


@subscription_bp.route("/api/v1/admin/subscriptions/", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.subscriptions.manage")
def admin_create_subscription():
    """
    Create subscription for user with auto-generated invoice.

    Body:
        - user_id: str (required, UUID)
        - plan_id or tarif_plan_id: str (required, UUID)
        - started_at: str (optional, ISO datetime, defaults to now)
        - status: str (optional, 'active' or 'trialing')
        - trial_days: int (optional, for trialing status)
        - billing_period_months: int (optional, override plan's billing period)

    Returns:
        201: Created subscription with invoice
        400: Validation error
        404: User or plan not found
        409: User already has active subscription
    """
    data = request.get_json() or {}

    # Validate required fields
    if not data.get("user_id"):
        return jsonify({"error": "user_id is required"}), 400

    # Accept both plan_id (frontend) and tarif_plan_id (legacy)
    plan_id = data.get("plan_id") or data.get("tarif_plan_id")
    if not plan_id:
        return jsonify({"error": "plan_id is required"}), 400

    # Parse started_at or default to now
    started_at_str = data.get("started_at")
    if started_at_str:
        try:
            started_at = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
            # Convert to naive UTC datetime
            if started_at.tzinfo:
                started_at = started_at.replace(tzinfo=None)
        except (ValueError, AttributeError):
            return jsonify({"error": "Invalid started_at format. Use ISO 8601."}), 400
    else:
        started_at = utcnow()

    requested_status = data.get("status", "active")

    user_repo = UserRepository(db.session)
    plan_repo = TarifPlanRepository(db.session)
    sub_repo = SubscriptionRepository(db.session)

    # Validate user exists
    user = user_repo.find_by_id(data["user_id"])
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Validate plan exists
    plan = plan_repo.find_by_id(plan_id)
    if not plan:
        return jsonify({"error": "Plan not found"}), 404

    # Check for existing active subscription
    existing = sub_repo.find_active_by_user(data["user_id"])
    if existing:
        return jsonify({"error": "User already has an active subscription"}), 409

    # Handle trialing status via SubscriptionService
    if requested_status == "trialing":
        sub_service = SubscriptionService(
            subscription_repo=sub_repo,
            tarif_plan_repo=plan_repo,
        )
        result = sub_service.start_trial(user.id, plan.id, user_repo)
        if not result.success:
            status_code = (
                409
                if "active subscription" in result.error.lower()
                or "already used" in result.error.lower()
                else 400
            )
            return jsonify({"error": result.error}), status_code

        subscription = result.subscription
        return (
            jsonify(
                {
                    "id": str(subscription.id),
                    "user_id": str(subscription.user_id),
                    "tarif_plan_id": str(subscription.tarif_plan_id),
                    "status": subscription.status.value,
                    "started_at": subscription.started_at.isoformat()
                    if subscription.started_at
                    else None,
                    "expires_at": subscription.expires_at.isoformat()
                    if subscription.expires_at
                    else None,
                    "trial_end_at": subscription.trial_end_at.isoformat()
                    if subscription.trial_end_at
                    else None,
                    "created_at": subscription.created_at.isoformat()
                    if subscription.created_at
                    else None,
                }
            ),
            201,
        )

    # Calculate expiration based on billing period
    billing_months = data.get("billing_period_months") or _get_billing_months(
        plan.billing_period
    )
    expires_at = started_at + relativedelta(months=billing_months)
    now = utcnow()
    status = (
        SubscriptionStatus.ACTIVE if started_at <= now else SubscriptionStatus.PENDING
    )
    if requested_status == "active":
        status = SubscriptionStatus.ACTIVE

    # Create subscription
    subscription = Subscription()
    subscription.user_id = user.id
    subscription.tarif_plan_id = plan.id
    subscription.status = status
    subscription.started_at = started_at
    subscription.expires_at = expires_at

    db.session.add(subscription)
    db.session.flush()

    # Create invoice
    invoice = UserInvoice()
    invoice.user_id = user.id
    invoice.tarif_plan_id = plan.id
    invoice.subscription_id = subscription.id
    invoice.invoice_number = UserInvoice.generate_invoice_number()
    invoice.amount = plan.price or plan.price_float or 0
    invoice.currency = plan.currency or "EUR"
    invoice.status = InvoiceStatus.PENDING
    invoice.invoiced_at = utcnow()
    invoice.expires_at = utcnow() + timedelta(days=30)
    db.session.add(invoice)

    db.session.commit()

    # Build response
    response = {
        "id": str(subscription.id),
        "user_id": str(subscription.user_id),
        "tarif_plan_id": str(subscription.tarif_plan_id),
        "status": subscription.status.value,
        "started_at": subscription.started_at.isoformat()
        if subscription.started_at
        else None,
        "expires_at": subscription.expires_at.isoformat()
        if subscription.expires_at
        else None,
        "created_at": subscription.created_at.isoformat()
        if subscription.created_at
        else None,
        "invoice": {
            "id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
            "amount": str(invoice.amount),
            "currency": invoice.currency,
            "status": invoice.status.value,
        },
    }

    return jsonify(response), 201


@subscription_bp.route("/api/v1/admin/subscriptions/", methods=["GET"])
@require_auth
@require_admin
@require_permission("subscription.subscriptions.view")
def admin_list_subscriptions():
    """
    List all subscriptions with pagination and filters.

    Query params:
        - limit: int (default 20, max 100)
        - offset: int (default 0)
        - status: str (active, pending, cancelled, expired)
        - user_id: str (filter by user)
        - plan_id: str (filter by plan)

    Returns:
        200: List of subscriptions with pagination info
    """
    limit = min(int(request.args.get("limit", 20)), 100)
    offset = int(request.args.get("offset", 0))
    status = request.args.get("status")
    user_id = request.args.get("user_id")
    plan_id = request.args.get("plan_id")
    plan_name = request.args.get("plan")  # Frontend sends plan name

    # Map American spelling to British for status
    if status == "canceled":
        status = "cancelled"

    sub_repo = SubscriptionRepository(db.session)
    user_repo = UserRepository(db.session)
    plan_repo = TarifPlanRepository(db.session)

    # If plan name is provided, find the plan_id
    if plan_name and not plan_id:
        plans = plan_repo.find_all()
        for plan in plans:
            if plan.name == plan_name:
                plan_id = str(plan.id)
                break

    subscriptions, total = sub_repo.find_all_paginated(
        limit=limit, offset=offset, status=status, user_id=user_id, plan_id=plan_id
    )

    # Enrich subscriptions with user and plan info for admin display
    result = []
    for sub in subscriptions:
        sub_dict = sub.to_dict()
        # Add user email
        user = user_repo.find_by_id(str(sub.user_id))
        sub_dict["user_email"] = user.email if user else ""
        # Add plan name
        plan = plan_repo.find_by_id(str(sub.tarif_plan_id))
        sub_dict["plan_name"] = plan.name if plan else ""
        # Add created_at for sorting
        sub_dict["created_at"] = sub.created_at.isoformat() if sub.created_at else None
        result.append(sub_dict)

    return (
        jsonify(
            {"subscriptions": result, "total": total, "limit": limit, "offset": offset}
        ),
        200,
    )


@subscription_bp.route("/api/v1/admin/subscriptions/<subscription_id>", methods=["GET"])
@require_auth
@require_admin
@require_permission("subscription.subscriptions.view")
def admin_get_subscription(subscription_id):
    """
    Get subscription detail with enriched user, plan, and payment data.

    Args:
        subscription_id: UUID of the subscription

    Returns:
        200: Subscription details with user, plan, payment info
        404: Subscription not found
    """
    from vbwd.repositories.invoice_repository import InvoiceRepository

    sub_repo = SubscriptionRepository(db.session)
    user_repo = UserRepository(db.session)
    plan_repo = TarifPlanRepository(db.session)
    invoice_repo = InvoiceRepository(db.session)

    subscription = sub_repo.find_by_id(subscription_id)

    if not subscription:
        return jsonify({"error": "Subscription not found"}), 404

    sub_dict = subscription.to_dict()

    # Enrich with user info
    user = user_repo.find_by_id(str(subscription.user_id))
    if user:
        sub_dict["user_email"] = user.email
        sub_dict["user_name"] = (
            (user.details.first_name + " " + user.details.last_name).strip()
            if user.details
            else ""
        )

    # Enrich with plan info
    plan = plan_repo.find_by_id(str(subscription.tarif_plan_id))
    if plan:
        sub_dict["plan_name"] = plan.name
        sub_dict["plan_description"] = plan.description
        sub_dict["plan_price"] = str(plan.price) if plan.price else None
        sub_dict["plan_billing_period"] = (
            plan.billing_period.value if plan.billing_period else None
        )

    # Map to frontend expected fields
    sub_dict["current_period_start"] = (
        subscription.started_at.isoformat() if subscription.started_at else None
    )
    sub_dict["current_period_end"] = (
        subscription.expires_at.isoformat() if subscription.expires_at else None
    )
    sub_dict["created_at"] = (
        subscription.created_at.isoformat() if subscription.created_at else None
    )
    sub_dict["trial_end_at"] = (
        subscription.trial_end_at.isoformat() if subscription.trial_end_at else None
    )
    sub_dict["is_trialing"] = subscription.is_trialing

    # Get payment history (invoices for this subscription)
    invoices = invoice_repo.find_by_subscription(str(subscription.id))
    payment_history = []
    for inv in invoices:
        payment_history.append(
            {
                "id": str(inv.id),
                "amount": float(inv.amount) if inv.amount else 0,
                "currency": inv.currency or "EUR",
                "status": inv.status.value if inv.status else "pending",
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
            }
        )
    sub_dict["payment_history"] = payment_history

    return jsonify({"subscription": sub_dict}), 200


@subscription_bp.route("/api/v1/admin/subscriptions/<subscription_id>/extend", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.subscriptions.manage")
def admin_extend_subscription(subscription_id):
    """
    Extend subscription expiration.

    Args:
        subscription_id: UUID of the subscription

    Body:
        - days: int (number of days to extend)

    Returns:
        200: Updated subscription
        404: Subscription not found
    """
    sub_repo = SubscriptionRepository(db.session)
    subscription = sub_repo.find_by_id(subscription_id)

    if not subscription:
        return jsonify({"error": "Subscription not found"}), 404

    data = request.get_json() or {}
    days = data.get("days", 30)

    if subscription.expires_at:
        subscription.expires_at = subscription.expires_at + timedelta(days=days)

    saved_sub = sub_repo.save(subscription)

    return (
        jsonify(
            {
                "subscription": saved_sub.to_dict(),
                "message": f"Subscription extended by {days} days",
            }
        ),
        200,
    )


@subscription_bp.route("/api/v1/admin/subscriptions/<subscription_id>/cancel", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.subscriptions.manage")
def admin_cancel_subscription(subscription_id):
    """
    Cancel a subscription.

    Args:
        subscription_id: UUID of the subscription

    Returns:
        200: Subscription cancelled
        404: Subscription not found
    """
    sub_repo = SubscriptionRepository(db.session)
    sub_service = SubscriptionService(subscription_repo=sub_repo)

    result = sub_service.cancel_subscription(subscription_id)

    if not result.success:
        if "not found" in result.error.lower():
            return jsonify({"error": result.error}), 404
        return jsonify({"error": result.error}), 400

    return (
        jsonify(
            {
                "subscription": result.subscription.to_dict(),
                "message": "Subscription cancelled",
            }
        ),
        200,
    )


@subscription_bp.route("/api/v1/admin/subscriptions/<subscription_id>/activate", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.subscriptions.manage")
def admin_activate_subscription(subscription_id):
    """
    Force activate a subscription.

    Args:
        subscription_id: UUID of the subscription

    Returns:
        200: Subscription activated
        404: Subscription not found
    """
    from vbwd.services.token_service import TokenService
    from vbwd.repositories.token_repository import (
        TokenBalanceRepository,
        TokenTransactionRepository,
    )
    from vbwd.repositories.token_bundle_purchase_repository import (
        TokenBundlePurchaseRepository,
    )

    sub_repo = SubscriptionRepository(db.session)
    token_service = TokenService(
        balance_repo=TokenBalanceRepository(db.session),
        transaction_repo=TokenTransactionRepository(db.session),
        purchase_repo=TokenBundlePurchaseRepository(db.session),
    )
    sub_service = SubscriptionService(
        subscription_repo=sub_repo, token_service=token_service
    )

    result = sub_service.activate_subscription(subscription_id)

    if not result.success:
        if "not found" in result.error.lower():
            return jsonify({"error": result.error}), 404
        return jsonify({"error": result.error}), 400

    return (
        jsonify(
            {
                "subscription": result.subscription.to_dict(),
                "message": "Subscription activated",
            }
        ),
        200,
    )


@subscription_bp.route("/api/v1/admin/subscriptions/<subscription_id>/refund", methods=["POST"])
@require_auth
@require_admin
@require_permission("subscription.subscriptions.manage")
def admin_refund_subscription(subscription_id):
    """
    Process refund for a subscription.

    Args:
        subscription_id: UUID of the subscription

    Body:
        - amount: decimal (optional, defaults to full amount)
        - reason: str (optional)

    Returns:
        200: Refund processed
        404: Subscription not found
    """
    sub_repo = SubscriptionRepository(db.session)
    subscription = sub_repo.find_by_id(subscription_id)

    if not subscription:
        return jsonify({"error": "Subscription not found"}), 404

    data = request.get_json() or {}
    reason = data.get("reason", "Admin refund")

    # Cancel the subscription as part of refund
    subscription.status = SubscriptionStatus.CANCELLED
    saved_sub = sub_repo.save(subscription)

    return (
        jsonify(
            {
                "subscription": saved_sub.to_dict(),
                "message": f"Refund processed: {reason}",
            }
        ),
        200,
    )
