"""Recurring-billing bus subscribers (S50.4).

Payment plugins (stripe/paypal/yookassa) publish domain-neutral recurring-billing
facts to the string-keyed event bus and never import the subscription domain.
This module subscribes to those facts and does the subscription work inline —
the bus dispatches synchronously, in the same request/transaction, so behaviour
and transactionality are preserved (no synchronous return is needed).

It replaces the old core ``ISubscriptionLifecycle`` port: the 5 lifecycle write
methods are now bus subscribers, keyed by event name. When the subscription
plugin is disabled, nothing subscribes and every published fact is a no-op — so
a payment install with no plan concept stays subscription-free.

Event taxonomy (payload keys are domain-neutral — ``provider_ref_id`` is the
provider's recurring object id, never ``provider_subscription_id``):

  * ``payment.provider_linked``  {invoice_id, provider, provider_ref_id}
  * ``payment.recurring_charge`` {provider, provider_ref_id, amount, currency,
                                  provider_reference, transaction_id, metadata}
  * ``payment.provider_cancelled`` {provider, provider_ref_id, reason}
  * ``payment.recurring_failed`` {provider, provider_ref_id, error_message}
  * ``payment.invoice_failed``   {invoice_id, provider, error_message, error_code}
"""
from decimal import Decimal
from uuid import UUID

from vbwd.plugins.payment_route_helpers import emit_payment_captured


class RecurringBillingSubscriber:
    """Performs subscription link/renew/cancel/fail from published payment facts.

    Behaviour is moved verbatim from the former ``SubscriptionLifecycle`` port
    implementation; only the entry points changed from method calls to bus
    callbacks (``(event_name, data)``).
    """

    def subscribe(self, event_bus) -> None:
        """Register every recurring-billing callback on the bus."""
        from vbwd.plugins.payment_route_helpers import (
            EVENT_PROVIDER_LINKED,
            EVENT_RECURRING_CHARGE,
            EVENT_PROVIDER_CANCELLED,
            EVENT_RECURRING_FAILED,
            EVENT_INVOICE_FAILED,
        )

        event_bus.subscribe(EVENT_PROVIDER_LINKED, self.on_provider_linked)
        event_bus.subscribe(EVENT_RECURRING_CHARGE, self.on_recurring_charge)
        event_bus.subscribe(EVENT_PROVIDER_CANCELLED, self.on_provider_cancelled)
        event_bus.subscribe(EVENT_RECURRING_FAILED, self.on_recurring_failed)
        event_bus.subscribe(EVENT_INVOICE_FAILED, self.on_invoice_failed)

    # -- bus plumbing ---------------------------------------------------------

    def _subscription_repo(self):
        from vbwd.extensions import db
        from plugins.subscription.subscription.repositories.subscription_repository import (  # noqa: E501
            SubscriptionRepository,
        )

        return SubscriptionRepository(db.session)

    def _invoice_repo(self):
        from vbwd.extensions import db
        from vbwd.repositories.invoice_repository import InvoiceRepository

        return InvoiceRepository(db.session)

    def _price_factory(self):
        from flask import current_app

        return current_app.container.price_factory()

    # -- subscribers ----------------------------------------------------------

    def on_provider_linked(self, _event_name: str, data: dict) -> None:
        """Record the provider's recurring id on the subscription for an invoice."""
        from vbwd.models.enums import LineItemType

        invoice = self._invoice_repo().find_by_id(UUID(str(data["invoice_id"])))
        if not invoice:
            return
        subscription_repo = self._subscription_repo()
        for line_item in invoice.line_items:
            if line_item.item_type == LineItemType.SUBSCRIPTION:
                subscription = subscription_repo.find_by_id(line_item.item_id)
                if subscription:
                    subscription.provider_subscription_id = data["provider_ref_id"]
                    subscription_repo.save(subscription)
                    break

    def on_recurring_charge(self, _event_name: str, data: dict) -> None:
        """Create the renewal invoice, then emit ``payment.captured`` for it.

        The forwarded ``metadata`` is passed verbatim to ``emit_payment_captured``
        so all downstream capture handling (mark paid, record payment, line-item
        period extension) is preserved byte-for-byte.
        """
        renewal_invoice_id = self._create_renewal_invoice(
            provider=data["provider"],
            provider_ref_id=data["provider_ref_id"],
            amount=data["amount"],
            currency=data["currency"],
            provider_reference=data["provider_reference"],
        )
        if renewal_invoice_id is None:
            return

        emit_payment_captured(
            invoice_id=renewal_invoice_id,
            payment_reference=data["provider_reference"],
            amount=data["amount"],
            currency=data["currency"],
            provider=data["provider"],
            transaction_id=data.get("transaction_id", ""),
            metadata=data.get("metadata") or {},
        )

    def on_provider_cancelled(self, _event_name: str, data: dict) -> None:
        """Cancel the subscription identified by the provider's recurring id."""
        from plugins.subscription.subscription.events import SubscriptionCancelledEvent

        provider = data["provider"]
        subscription = self._subscription_repo().find_by_provider_subscription_id(
            data["provider_ref_id"]
        )
        if not subscription:
            return
        event = SubscriptionCancelledEvent(
            subscription_id=subscription.id,
            user_id=subscription.user_id,
            reason=data.get("reason") or f"{provider}_subscription_cancelled",
            provider=provider,
        )
        self._emit(event)

    def on_recurring_failed(self, _event_name: str, data: dict) -> None:
        """Flag a failed recurring charge for the matching subscription."""
        subscription = self._subscription_repo().find_by_provider_subscription_id(
            data["provider_ref_id"]
        )
        if not subscription:
            return
        self._flag_payment_failed(subscription)

    def on_invoice_failed(self, _event_name: str, data: dict) -> None:
        """Flag payment failure for the subscription on an invoice."""
        from vbwd.models.enums import LineItemType

        invoice = self._invoice_repo().find_by_id(UUID(str(data["invoice_id"])))
        if not invoice:
            return
        subscription_repo = self._subscription_repo()
        for line_item in invoice.line_items:
            if line_item.item_type == LineItemType.SUBSCRIPTION:
                subscription = subscription_repo.find_by_id(line_item.item_id)
                if subscription:
                    self._flag_payment_failed(subscription)
                break

    # -- helpers --------------------------------------------------------------

    def _flag_payment_failed(self, subscription) -> None:
        """Set ``payment_failed_at`` once (idempotent) and persist."""
        from vbwd.utils.datetime_utils import utcnow

        if subscription.payment_failed_at is None:
            subscription.payment_failed_at = utcnow()
            self._subscription_repo().save(subscription)

    def _create_renewal_invoice(
        self,
        provider: str,
        provider_ref_id: str,
        amount,
        currency: str,
        provider_reference: str,
    ):
        """Create a renewal invoice for the matching subscription.

        Returns the renewal invoice id, or ``None`` when there is no matching
        subscription or the provider invoice was already processed (dedup).
        """
        from vbwd.models.enums import LineItemType, InvoiceStatus
        from vbwd.models.invoice import UserInvoice
        from vbwd.models.invoice_line_item import InvoiceLineItem

        subscription_repo = self._subscription_repo()
        subscription = subscription_repo.find_by_provider_subscription_id(
            provider_ref_id
        )
        if not subscription:
            return None

        invoice_repo = self._invoice_repo()
        existing = invoice_repo.find_by_provider_session_id(provider_reference)
        if existing:
            return existing.id

        plan = subscription.tarif_plan
        # S85.2 (D8): the renewal charge is the amount the provider actually
        # billed (from the recurring-charge webhook) — already the gross the
        # customer paid. It stays authoritative for the recorded total; the
        # charged gross is NOT re-derived from the plan price.
        charged_amount = Decimal(str(amount))
        # S85.4: but the tax DISCLOSURE is still derived from the plan's Price
        # split and reconciled to the charged gross, so the renewal invoice
        # carries a real per-rate breakdown (net + Σtax == charged gross).
        net_amount, tax_amount, tax_breakdown = self._renewal_tax_split(
            plan, charged_amount
        )
        renewal_invoice = UserInvoice(
            user_id=subscription.user_id,
            invoice_number=UserInvoice.generate_invoice_number(),
            amount=charged_amount,
            subtotal=net_amount,
            tax_amount=tax_amount,
            total_amount=charged_amount,
            currency=(currency or "eur").upper(),
            status=InvoiceStatus.PENDING,
            payment_method=provider,
            provider_session_id=provider_reference,
        )
        # The subscription/plan link is the SUBSCRIPTION line item appended below
        # (item_id == subscription.id), not a column on the invoice.
        renewal_invoice.line_items.append(
            InvoiceLineItem(
                item_type=LineItemType.SUBSCRIPTION,
                item_id=subscription.id,
                description=(
                    f"Renewal: {plan.name}" if plan else "Subscription renewal"
                ),
                quantity=1,
                unit_price=charged_amount,
                total_price=charged_amount,
                net_amount=net_amount,
                tax_amount=tax_amount,
                tax_breakdown=tax_breakdown,
            )
        )
        invoice_repo.save(renewal_invoice)
        return renewal_invoice.id

    _CENTS = Decimal("0.01")

    def _renewal_tax_split(self, plan, charged_amount):
        """Reconcile the plan's tax split to the provider's charged gross.

        Returns ``(net_amount, tax_amount, tax_breakdown)`` whose net + Σtax
        equals ``charged_amount`` exactly. The plan's ``Price`` gives the tax
        ratio (which rates apply, in what proportion); that ratio is applied to
        the authoritative charged gross so a small provider/plan price drift
        never leaves net + tax disagreeing with the recorded total. A plan with
        no taxes (or no plan) ⇒ net == gross, empty breakdown.
        """
        from decimal import ROUND_HALF_UP

        if plan is None:
            return charged_amount, Decimal("0.00"), []

        computed_price = self._price_factory().get_price_from_object(plan)
        if not computed_price.taxes:
            return charged_amount, Decimal("0.00"), []

        gross = Decimal(str(computed_price.brutto))
        if gross == 0:
            return charged_amount, Decimal("0.00"), []

        # Scale each per-rate amount by (charged_gross / plan_gross) so the
        # split tracks the authoritative charged amount.
        scale = charged_amount / gross
        breakdown = []
        tax_total = Decimal("0.00")
        for tax in computed_price.taxes:
            scaled = (Decimal(str(tax.amount)) * scale).quantize(
                self._CENTS, rounding=ROUND_HALF_UP
            )
            breakdown.append(
                {"code": tax.code, "rate": tax.rate, "amount": float(scaled)}
            )
            tax_total += scaled
        # Net is the remainder so net + Σtax == charged gross exactly (the tax
        # rounding residue lands in net, never breaking the invariant).
        net_amount = charged_amount - tax_total
        return net_amount, tax_total, breakdown

    def _emit(self, event) -> None:
        from flask import current_app

        current_app.container.event_dispatcher().emit(event)
