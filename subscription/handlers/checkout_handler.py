"""Checkout event handler."""
from typing import List, Dict, Any, cast
from uuid import uuid4, UUID
from decimal import Decimal
from vbwd.events.domain import IEventHandler, DomainEvent, EventResult
from vbwd.events.checkout_events import CheckoutRequestedEvent
from vbwd.models.enums import (
    SubscriptionStatus,
    PurchaseStatus,
    InvoiceStatus,
    LineItemType,
)
from plugins.subscription.subscription.models import Subscription
from vbwd.models.invoice import UserInvoice
from vbwd.models.invoice_line_item import InvoiceLineItem
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

                # Get plan price
                if plan.price_obj:
                    plan_price = plan.price_obj.price_decimal
                elif plan.price:
                    plan_price = plan.price
                else:
                    plan_price = Decimal(str(plan.price_float))

                # Create subscription: TRIALING if plan has trial days, else PENDING
                subscription = Subscription(
                    id=uuid4(),
                    user_id=event.user_id,
                    tarif_plan_id=event.plan_id,
                    status=SubscriptionStatus.PENDING,
                )
                if plan.trial_days and plan.trial_days > 0:
                    subscription.start_trial(plan.trial_days)
                repos["subscription"].save(subscription)

                # Add subscription line item
                line_items_data.append(
                    {
                        "type": LineItemType.SUBSCRIPTION.value,
                        "item_id": subscription.id,
                        "description": plan.name,
                        "unit_price": plan_price,
                        "total_price": plan_price,
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

                purchase = TokenBundlePurchase(
                    id=uuid4(),
                    user_id=event.user_id,
                    bundle_id=bundle_id,
                    status=PurchaseStatus.PENDING,
                    tokens_credited=False,
                    token_amount=bundle.token_amount,
                    price=bundle.price,
                )
                repos["token_bundle_purchase"].create(purchase)
                bundle_purchases.append(purchase)

                # Add bundle line item
                line_items_data.append(
                    {
                        "type": LineItemType.TOKEN_BUNDLE.value,
                        "item_id": purchase.id,
                        "description": bundle.name,
                        "unit_price": bundle.price,
                        "total_price": bundle.price,
                    }
                )
                total_amount += bundle.price

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

                # Add add-on line item
                line_items_data.append(
                    {
                        "type": LineItemType.ADD_ON.value,
                        "item_id": addon_sub.id,
                        "description": addon.name,
                        "unit_price": addon.price,
                        "total_price": addon.price,
                    }
                )
                total_amount += addon.price

            # 4. Create invoice with all line items
            invoice = UserInvoice(
                id=uuid4(),
                user_id=event.user_id,
                tarif_plan_id=event.plan_id,
                subscription_id=subscription.id if subscription else None,
                invoice_number=UserInvoice.generate_invoice_number(),
                amount=total_amount,
                subtotal=total_amount,
                total_amount=total_amount,
                currency=event.currency,
                status=InvoiceStatus.PENDING,
                payment_method=event.payment_method_code,
            )
            repos["invoice"].save(invoice)

            # 5. Create line items
            for item_data in line_items_data:
                line_item = InvoiceLineItem(
                    id=uuid4(),
                    invoice_id=invoice.id,
                    item_type=item_data["type"],
                    item_id=item_data["item_id"],
                    description=item_data["description"],
                    quantity=1,
                    unit_price=item_data["unit_price"],
                    total_price=item_data["total_price"],
                )
                repos["invoice_line_item"].create(line_item)

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
