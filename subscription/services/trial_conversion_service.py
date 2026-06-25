"""S103.2d — convert expired trials by charging the checkout-selected method.

At trial-end this service builds a PENDING renewal invoice and re-charges the
``RecurringChargeProvider`` plugin behind the method the user picked at checkout:

  * success → the provider captured the invoice (emitting ``payment.captured``),
    which already flipped the subscription to ACTIVE via the line-item handler —
    the service does NOT activate again.
  * failure → the subscription is cancelled, ``payment_failed_at`` stamped, and a
    ``subscription.cancelled`` event published.
  * no charger (e.g. a manual "invoice" method with no recurring capability) →
    the subscription lapses to CANCELLED and the PENDING invoice is kept (no
    failure stamp — nothing was charged), preserving the legacy manual flow.

Single responsibility: trial-end behaviour. Collaborators are injected so the
unit tests run without a Flask app; ``build_trial_conversion_service`` is the
production wiring.
"""
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Dict, List, Optional

from vbwd.models.enums import InvoiceStatus, LineItemType
from vbwd.models.invoice import UserInvoice
from vbwd.models.invoice_line_item import InvoiceLineItem
from vbwd.plugins.payment_provider import RecurringChargeProvider
from vbwd.utils.datetime_utils import utcnow

from plugins.subscription.subscription.services.lifecycle_events import (
    EVENT_SUBSCRIPTION_CANCELLED,
    publish_subscription_event,
)

_CENTS = Decimal("0.01")


class TrialConversionService:
    """Charge each expired trial's saved method and convert to ACTIVE."""

    def __init__(
        self,
        subscription_repo: Any,
        invoice_repo: Any,
        price_factory: Any,
        charger_resolver: Callable[[Optional[str]], Optional[RecurringChargeProvider]],
        event_publisher: Callable[[str, Any, Any], None] = publish_subscription_event,
    ):
        """Initialize with injected collaborators.

        Args:
            subscription_repo: SubscriptionRepository (``find_expired_trials`` /
                ``save``).
            invoice_repo: InvoiceRepository (``save``).
            price_factory: Core PriceFactory (``get_price_from_object``).
            charger_resolver: Maps a method code → its RecurringChargeProvider
                (or ``None`` when not chargeable).
            event_publisher: Publishes a lifecycle event; defaults to the shared
                ``publish_subscription_event`` (injectable for tests).
        """
        self._subscription_repo = subscription_repo
        self._invoice_repo = invoice_repo
        self._price_factory = price_factory
        self._charger_resolver = charger_resolver
        self._event_publisher = event_publisher

    def convert_expired_trials(self, now=None) -> List[Dict[str, Any]]:
        """Process every expired trial; return a per-subscription outcome list."""
        clock = now or utcnow()
        results: List[Dict[str, Any]] = []
        for subscription in self._subscription_repo.find_expired_trials(now=clock):
            results.append(self._convert_one(subscription, clock))
        return results

    def _convert_one(self, subscription, clock) -> Dict[str, Any]:
        invoice = self._create_pending_renewal_invoice(subscription)
        charger = self._charger_resolver(subscription.payment_method)

        if charger is None:
            # Manual / non-recurring method: the trial lapses and the PENDING
            # invoice remains for manual settlement (no charge failed here, so
            # no payment_failed_at). Still publish CANCELLED so RBAC reconciles
            # and the lapsed user loses plan permissions (S69 D5 — same guarantee
            # the legacy cancel-only expire_trials gave every cancelled trial).
            subscription.cancel()
            self._subscription_repo.save(subscription)
            self._event_publisher(
                EVENT_SUBSCRIPTION_CANCELLED, subscription, subscription.user_id
            )
            return {
                "subscription_id": str(subscription.id),
                "invoice_id": str(invoice.id),
                "outcome": "no_charger",
            }

        result = charger.charge_saved_method(
            user_id=subscription.user_id, invoice=invoice
        )
        if result.success:
            # The provider captured the invoice; the line-item handler already
            # flipped the subscription to ACTIVE. Nothing more to do here.
            return {
                "subscription_id": str(subscription.id),
                "invoice_id": str(invoice.id),
                "outcome": "charged",
            }

        # Declined / insufficient: cancel, stamp the failure, publish.
        subscription.cancel()
        subscription.payment_failed_at = clock
        self._subscription_repo.save(subscription)
        self._event_publisher(
            EVENT_SUBSCRIPTION_CANCELLED, subscription, subscription.user_id
        )
        return {
            "subscription_id": str(subscription.id),
            "invoice_id": str(invoice.id),
            "outcome": "charge_failed",
            "error": result.error,
        }

    def _create_pending_renewal_invoice(self, subscription) -> UserInvoice:
        """Build + persist a PENDING renewal invoice for the subscription.

        Mirrors the recurring-billing renewal pattern: the gross is the plan's
        computed ``Price.brutto`` (quantized to cents), currency comes from the
        same Price, and a per-rate tax split is recorded on the SUBSCRIPTION line
        item (item_id == subscription.id) so the invoice carries a real
        net/tax/gross breakdown.
        """
        plan = subscription.tarif_plan
        computed_price = self._price_factory.get_price_from_object(plan)
        gross = Decimal(str(computed_price.brutto)).quantize(
            _CENTS, rounding=ROUND_HALF_UP
        )
        currency = (getattr(computed_price, "currency", None) or "EUR").upper()
        net_amount, tax_amount, tax_breakdown = self._tax_split(computed_price, gross)

        invoice = UserInvoice(
            user_id=subscription.user_id,
            invoice_number=UserInvoice.generate_invoice_number(),
            amount=gross,
            subtotal=net_amount,
            tax_amount=tax_amount,
            total_amount=gross,
            currency=currency,
            status=InvoiceStatus.PENDING,
            payment_method=subscription.payment_method,
        )
        invoice.line_items.append(
            InvoiceLineItem(
                item_type=LineItemType.SUBSCRIPTION,
                item_id=subscription.id,
                description=(
                    f"Renewal: {plan.name}" if plan else "Subscription renewal"
                ),
                quantity=1,
                unit_price=gross,
                total_price=gross,
                net_amount=net_amount,
                tax_amount=tax_amount,
                tax_breakdown=tax_breakdown,
            )
        )
        return self._invoice_repo.save(invoice)

    def _tax_split(self, computed_price, gross):
        """Return ``(net_amount, tax_amount, tax_breakdown)`` summing to gross.

        No taxes (or zero gross) ⇒ net == gross, zero tax, empty breakdown. The
        tax rounding residue lands in net so net + Σtax == gross exactly.
        """
        taxes = getattr(computed_price, "taxes", None) or []
        plan_gross = Decimal(str(computed_price.brutto))
        if not taxes or plan_gross == 0:
            return gross, Decimal("0.00"), []

        scale = gross / plan_gross
        breakdown = []
        tax_total = Decimal("0.00")
        for tax in taxes:
            scaled = (Decimal(str(tax.amount)) * scale).quantize(
                _CENTS, rounding=ROUND_HALF_UP
            )
            breakdown.append(
                {"code": tax.code, "rate": tax.rate, "amount": float(scaled)}
            )
            tax_total += scaled
        net_amount = gross - tax_total
        return net_amount, tax_total, breakdown


def build_trial_conversion_service() -> TrialConversionService:
    """Production wiring: build the service from ``current_app`` collaborators."""
    from flask import current_app

    from vbwd.extensions import db
    from vbwd.repositories.invoice_repository import InvoiceRepository

    from plugins.subscription.subscription.repositories.subscription_repository import (
        SubscriptionRepository,
    )
    from plugins.subscription.subscription.services.recurring_charge_resolver import (
        build_recurring_charge_resolver,
    )

    return TrialConversionService(
        subscription_repo=SubscriptionRepository(db.session),
        invoice_repo=InvoiceRepository(db.session),
        price_factory=current_app.container.price_factory(),
        charger_resolver=build_recurring_charge_resolver(),
    )
