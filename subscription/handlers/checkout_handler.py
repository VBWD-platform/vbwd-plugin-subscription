"""Checkout event handler."""
from typing import List, Dict, Any, cast
from uuid import uuid4, UUID
from decimal import Decimal, ROUND_HALF_UP
from vbwd.events.domain import IEventHandler, DomainEvent, EventResult
from plugins.subscription.subscription.events import CheckoutRequestedEvent
from vbwd.models.enums import (
    SubscriptionStatus,
    PurchaseStatus,
    InvoiceStatus,
    LineItemType,
)
from plugins.subscription.subscription.constants import VENDOR_ID_KEY
from plugins.subscription.subscription.models import Subscription
from plugins.subscription.subscription.services.plugin_config import (
    marketplace_enabled,
)
from vbwd.models.invoice import UserInvoice
from vbwd.models.invoice_line_item import InvoiceLineItem
from vbwd.services.invoice_line_item_snapshot import (
    snapshot_line_item_tags_and_custom_fields,
)
from vbwd.models.token_bundle_purchase import TokenBundlePurchase
from plugins.subscription.subscription.models import AddOnSubscription


class CheckoutHandler(IEventHandler):
    """
    Handler for checkout requests.

    Creates all items (subscription, token bundles, add-ons) as PENDING
    until payment is confirmed.
    """

    def __init__(self, container):
        """
        Initialize handler with DI container.

        Args:
            container: DI container to get repositories at request time
        """
        self._container = container

    def _get_repos(self):
        """Get fresh repositories for the current request session."""
        return {
            "subscription": self._container.subscription_repository(),
            "tarif_plan": self._container.tarif_plan_repository(),
            "tarif_plan_category": self._container.tarif_plan_category_repository(),
            "token_bundle": self._container.token_bundle_repository(),
            "token_bundle_purchase": self._container.token_bundle_purchase_repository(),
            "addon": self._container.addon_repository(),
            "addon_subscription": self._container.addon_subscription_repository(),
            "invoice": self._container.invoice_repository(),
            "invoice_line_item": self._container.invoice_line_item_repository(),
        }

    _CENTS = Decimal("0.01")

    def _charge_for(self, priceable):
        """Resolve (unit_price, price_breakdown, tax_fields) for a sellable.

        S85.2 (D1/D8): the charged amount is the computed ``Price.brutto``,
        resolved through the single core ``PriceFactory`` (honours the global
        ``prices_mode_in_db``). The invoice is an immutable financial record
        (Numeric(10,2) columns), so the brutto float is quantized to cents here
        — the one legitimate rounding boundary (the live VO stays full-precision,
        D4). S85.4: ``tax_fields`` carries the recorded ``net_amount`` /
        ``tax_amount`` / ``tax_breakdown`` (per-rate) for first-class persistence.
        """
        from vbwd.pricing.line_tax_fields import line_tax_fields

        computed_price = self._container.price_factory().get_price_from_object(
            priceable
        )
        unit_price = Decimal(str(computed_price.brutto)).quantize(
            self._CENTS, rounding=ROUND_HALF_UP
        )
        return unit_price, computed_price.to_dict(), line_tax_fields(computed_price)

    def can_handle(self, event: DomainEvent) -> bool:
        """Check if this handler can handle checkout.requested events."""
        return isinstance(event, CheckoutRequestedEvent)

    def handle(self, event: DomainEvent) -> EventResult:
        """
        Handle checkout request.

        Creates:
        1. PENDING subscription
        2. PENDING token bundle purchases (if any)
        3. PENDING add-on subscriptions (if any)
        4. Invoice with all line items

        Args:
            event: CheckoutRequestedEvent

        Returns:
            EventResult with created items or error
        """
        if not isinstance(event, CheckoutRequestedEvent):
            return EventResult.error_result("Invalid event type")

        try:
            # Get fresh repositories for current request session
            repos = self._get_repos()

            line_items_data: List[Dict[str, Any]] = []
            total_amount = Decimal("0.00")
            subscription = None
            plan = None

            # 1. If plan_id provided, validate plan and create subscription
            if event.plan_id:
                plan = repos["tarif_plan"].find_by_id(event.plan_id)
                if not plan:
                    return EventResult.error_result("Plan not found")
                if not plan.is_active:
                    return EventResult.error_result("Plan is not active")

                # Check is_single category enforcement
                for category in getattr(plan, "categories", []):
                    if category.is_single:
                        category_plan_ids = [str(p.id) for p in category.tarif_plans]
                        existing = repos[
                            "subscription"
                        ].find_active_by_user_in_category(
                            event.user_id, category_plan_ids
                        )
                        if existing:
                            return EventResult.error_result(
                                f"User already has an active subscription in "
                                f"category '{category.name}'. "
                                f"Please upgrade or downgrade instead."
                            )

                # S85.2 (D8): the charged plan price is the computed brutto
                # (PriceFactory honours the global prices_mode_in_db). The
                # per-line netto + tax breakdown is recorded on the line item.
                plan_price, plan_breakdown, plan_tax_fields = self._charge_for(plan)

                # Create subscription: TRIALING if plan has trial days, else PENDING
                subscription = Subscription(
                    id=uuid4(),
                    user_id=event.user_id,
                    tarif_plan_id=event.plan_id,
                    status=SubscriptionStatus.PENDING,
                    # S103.2a: remember the method the user picked so trial-end
                    # conversion can re-charge the same saved method.
                    payment_method=event.payment_method_code,
                )
                if plan.trial_days and plan.trial_days > 0:
                    subscription.start_trial(plan.trial_days)
                repos["subscription"].save(subscription)

                # Add subscription line item
                subscription_extra_data: Dict[str, Any] = {
                    "price_breakdown": plan_breakdown
                }
                # Vendor-mode (marketplace): stamp the selling vendor's user id
                # onto the buyer invoice line so the central marketplace plugin
                # credits the vendor on ``invoice.paid``. Merged (never clobbers
                # other keys), only for vendor-owned plans, only when vendor-mode
                # is enabled — subscription stamps a LOCAL literal and never
                # imports marketplace.
                if plan.vendor_id is not None and marketplace_enabled():
                    subscription_extra_data[VENDOR_ID_KEY] = str(plan.vendor_id)
                line_items_data.append(
                    {
                        "type": LineItemType.SUBSCRIPTION.value,
                        "item_id": subscription.id,
                        "description": plan.name,
                        "unit_price": plan_price,
                        "total_price": plan_price,
                        "tax_fields": plan_tax_fields,
                        "extra_data": subscription_extra_data,
                    }
                )
                total_amount += plan_price

            # 2. Create PENDING token bundle purchases
            bundle_purchases: List[TokenBundlePurchase] = []
            for bundle_id in event.token_bundle_ids:
                bundle = repos["token_bundle"].find_by_id(bundle_id)
                if not bundle:
                    return EventResult.error_result(
                        f"Token bundle {bundle_id} not found"
                    )
                if not bundle.is_active:
                    return EventResult.error_result(
                        f"Token bundle {bundle.name} is not active"
                    )

                # S85.2 (D8): the charged bundle price is the computed brutto.
                bundle_price, bundle_breakdown, bundle_tax_fields = self._charge_for(
                    bundle
                )
                purchase = TokenBundlePurchase(
                    id=uuid4(),
                    user_id=event.user_id,
                    bundle_id=bundle_id,
                    status=PurchaseStatus.PENDING,
                    tokens_credited=False,
                    token_amount=bundle.token_amount,
                    price=bundle_price,
                )
                repos["token_bundle_purchase"].create(purchase)
                bundle_purchases.append(purchase)

                # Add bundle line item
                line_items_data.append(
                    {
                        "type": LineItemType.TOKEN_BUNDLE.value,
                        "item_id": purchase.id,
                        "description": bundle.name,
                        "unit_price": bundle_price,
                        "total_price": bundle_price,
                        "tax_fields": bundle_tax_fields,
                        "extra_data": {"price_breakdown": bundle_breakdown},
                    }
                )
                total_amount += bundle_price

            # 3. Create PENDING add-on subscriptions
            addon_subscriptions: List[AddOnSubscription] = []
            for addon_id in event.add_on_ids:
                addon = repos["addon"].find_by_id(addon_id)
                if not addon:
                    return EventResult.error_result(f"Add-on {addon_id} not found")
                if not addon.is_active:
                    return EventResult.error_result(
                        f"Add-on {addon.name} is not active"
                    )

                addon_sub = AddOnSubscription(
                    id=uuid4(),
                    user_id=event.user_id,
                    addon_id=addon_id,
                    subscription_id=subscription.id if subscription else None,
                    status=SubscriptionStatus.PENDING,
                )
                repos["addon_subscription"].create(addon_sub)
                addon_subscriptions.append(addon_sub)

                # S85.2 (D8): the charged add-on price is the computed brutto.
                addon_price, addon_breakdown, addon_tax_fields = self._charge_for(addon)
                # Add add-on line item
                line_items_data.append(
                    {
                        "type": LineItemType.ADD_ON.value,
                        "item_id": addon_sub.id,
                        "description": addon.name,
                        "unit_price": addon_price,
                        "total_price": addon_price,
                        "tax_fields": addon_tax_fields,
                        "extra_data": {"price_breakdown": addon_breakdown},
                    }
                )
                total_amount += addon_price

            # 4a. Apply a coupon discount via the generic core seam. The
            #     discount plugin registers the adjustment; core/subscription
            #     name no discount domain. Empty registry / no code → no-op.
            from vbwd.services.checkout_price_adjustment_registry import (
                resolve_price_adjustment,
            )

            price_result = resolve_price_adjustment(
                code=event.coupon_code,
                subtotal=total_amount,
                user_id=str(event.user_id) if event.user_id else None,
                scope="SUBSCRIPTION",
                currency=event.currency,
            )
            if not price_result.valid:
                return EventResult.error_result(
                    price_result.error or "Coupon is not valid"
                )
            if price_result.discount_amount > Decimal("0.00"):
                # Negative line keeps sum(line_items) == total_amount (audit-
                # friendly, no schema change). CUSTOM-typed, flagged in metadata.
                line_items_data.append(
                    {
                        "type": LineItemType.CUSTOM.value,
                        "item_id": uuid4(),
                        "description": price_result.label or "Discount",
                        "unit_price": -price_result.discount_amount,
                        "total_price": -price_result.discount_amount,
                        "extra_data": {
                            "discount": True,
                            "coupon_code": event.coupon_code,
                        },
                    }
                )
                total_amount -= price_result.discount_amount

            # 4. Create invoice with all line items. The subscription/plan link
            #    is carried by the SUBSCRIPTION line item below, not a column.
            #    S85.4: roll the net / tax / gross totals up from the per-line
            #    tax fields so the invoice carries a real tax split. Lines
            #    without a breakdown (e.g. the discount line) default to
            #    net == gross, zero tax.
            invoice_net = Decimal("0.00")
            invoice_tax = Decimal("0.00")
            for item_data in line_items_data:
                tax_fields = item_data.get("tax_fields")
                if tax_fields:
                    invoice_net += tax_fields["net_amount"]
                    invoice_tax += tax_fields["tax_amount"]
                else:
                    invoice_net += item_data["total_price"]

            invoice = UserInvoice(
                id=uuid4(),
                user_id=event.user_id,
                invoice_number=UserInvoice.generate_invoice_number(),
                amount=total_amount,
                subtotal=invoice_net,
                tax_amount=invoice_tax,
                total_amount=total_amount,
                currency=event.currency,
                status=InvoiceStatus.PENDING,
                payment_method=event.payment_method_code,
            )
            repos["invoice"].save(invoice)

            # 5. Create line items
            for item_data in line_items_data:
                tax_fields = item_data.get("tax_fields") or {}
                line_item = InvoiceLineItem(
                    id=uuid4(),
                    invoice_id=invoice.id,
                    item_type=item_data["type"],
                    item_id=item_data["item_id"],
                    description=item_data["description"],
                    quantity=1,
                    unit_price=item_data["unit_price"],
                    total_price=item_data["total_price"],
                    net_amount=tax_fields.get("net_amount"),
                    tax_amount=tax_fields.get("tax_amount"),
                    tax_breakdown=tax_fields.get("tax_breakdown"),
                    extra_data=item_data.get("extra_data"),
                )
                repos["invoice_line_item"].create(line_item)
                # S77: freeze the source plan/add-on's tags + custom-fields onto
                # the line item so the invoice stays immutable (no live join).
                snapshot_line_item_tags_and_custom_fields(line_item)

            # 5a. Redeem the coupon + record the application, now the invoice
            #     exists. Runs exactly once; no-op when no coupon was applied.
            if price_result.on_committed:
                price_result.on_committed(str(invoice.id), str(event.user_id))

            # 6. Update purchases and addon subscriptions with invoice_id
            for purchase in bundle_purchases:
                purchase.invoice_id = invoice.id
                repos["token_bundle_purchase"].save(purchase)

            for addon_sub in addon_subscriptions:
                addon_sub.invoice_id = invoice.id
                repos["addon_subscription"].save(addon_sub)

            # Reload invoice to get line items
            invoice = repos["invoice"].find_by_id(invoice.id)

            # 7. Auto-pay if total is zero (free plan / promotional)
            if total_amount == Decimal("0.00"):
                from vbwd.events.payment_events import PaymentCapturedEvent

                payment_event = PaymentCapturedEvent(
                    invoice_id=cast(UUID, invoice.id),
                    payment_reference="zero-price",
                    amount="0.00",
                    currency=event.currency,
                    user_id=event.user_id,
                )
                dispatcher = self._container.event_dispatcher()
                dispatcher.emit(payment_event)
                # Reload to reflect PAID status set by PaymentCapturedHandler
                invoice = repos["invoice"].find_by_id(invoice.id)

            # Build response
            message = (
                "Checkout complete. Free plan activated."
                if total_amount == Decimal("0.00")
                else "Checkout created. Awaiting payment."
            )
            result_data: Dict[str, Any] = {
                "invoice": invoice.to_dict(),
                "token_bundles": [p.to_dict() for p in bundle_purchases],
                "add_ons": [a.to_dict() for a in addon_subscriptions],
                "message": message,
            }

            if subscription and plan:
                result_data["subscription"] = self._subscription_to_dict(
                    subscription, plan
                )

            return EventResult.success_result(result_data)

        except Exception as e:
            return EventResult.error_result(str(e))

    def _subscription_to_dict(self, subscription: Subscription, plan) -> Dict[str, Any]:
        """Convert subscription to dict with plan info."""
        result = subscription.to_dict()
        result["id"] = str(subscription.id)
        result["plan"] = {
            "id": str(plan.id),
            "name": plan.name,
            "slug": plan.slug,
        }
        return result
