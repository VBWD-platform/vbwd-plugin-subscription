"""Subscription lifecycle — implements the core ISubscriptionLifecycle port.

Consolidates the recurring-subscription logic that used to live in each payment
plugin's webhook handlers (link / renewal-invoice / cancel / payment-failed),
so stripe/paypal/yookassa no longer import the subscription model or repo.
Behaviour is moved verbatim (E2).
"""
from decimal import Decimal
from typing import Optional, Union
from uuid import UUID

from vbwd.services.subscription_lifecycle import ISubscriptionLifecycle


class SubscriptionLifecycle(ISubscriptionLifecycle):
    """Recurring-subscription writes for payment-provider webhooks."""

    def _container(self):
        from flask import current_app

        return current_app.container

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

    def link_provider_subscription(
        self, invoice_id: UUID, provider_subscription_id: str
    ) -> None:
        from vbwd.models.enums import LineItemType

        invoice = self._invoice_repo().find_by_id(invoice_id)
        if not invoice:
            return
        sub_repo = self._subscription_repo()
        for line_item in invoice.line_items:
            if line_item.item_type == LineItemType.SUBSCRIPTION:
                subscription = sub_repo.find_by_id(line_item.item_id)
                if subscription:
                    subscription.provider_subscription_id = provider_subscription_id
                    sub_repo.save(subscription)
                    break

    def record_provider_renewal(
        self,
        provider: str,
        provider_subscription_id: str,
        amount: Union[Decimal, str],
        currency: str,
        provider_reference: str,
    ) -> Optional[UUID]:
        from vbwd.models.enums import LineItemType, InvoiceStatus
        from vbwd.models.invoice import UserInvoice
        from vbwd.models.invoice_line_item import InvoiceLineItem

        sub_repo = self._subscription_repo()
        subscription = sub_repo.find_by_provider_subscription_id(
            provider_subscription_id
        )
        if not subscription:
            return None

        invoice_repo = self._invoice_repo()
        # Deduplication: skip if this provider invoice was already processed.
        existing = invoice_repo.find_by_provider_session_id(provider_reference)
        if existing:
            return existing.id

        plan = subscription.tarif_plan
        amt = Decimal(str(amount))
        # The subscription/plan link is the SUBSCRIPTION line item appended
        # below (item_id == subscription.id), not a column on the invoice.
        renewal_invoice = UserInvoice(
            user_id=subscription.user_id,
            invoice_number=UserInvoice.generate_invoice_number(),
            amount=amt,
            total_amount=amt,
            currency=(currency or "eur").upper(),
            status=InvoiceStatus.PENDING,
            payment_method=provider,
            provider_session_id=provider_reference,
        )
        renewal_invoice.line_items.append(
            InvoiceLineItem(
                item_type=LineItemType.SUBSCRIPTION,
                item_id=subscription.id,
                description=f"Renewal: {plan.name}" if plan else "Subscription renewal",
                quantity=1,
                unit_price=amt,
                total_price=amt,
            )
        )
        invoice_repo.save(renewal_invoice)
        return renewal_invoice.id

    def cancel_by_provider_subscription_id(
        self, provider: str, provider_subscription_id: str, reason: Optional[str] = None
    ) -> None:
        from vbwd.events.payment_events import SubscriptionCancelledEvent

        subscription = self._subscription_repo().find_by_provider_subscription_id(
            provider_subscription_id
        )
        if not subscription:
            return
        event = SubscriptionCancelledEvent(
            subscription_id=subscription.id,
            user_id=subscription.user_id,
            reason=reason or f"{provider}_subscription_cancelled",
            provider=provider,
        )
        self._container().event_dispatcher().emit(event)

    def mark_provider_payment_failed(
        self, provider: str, provider_subscription_id: str, error_message: str
    ) -> None:
        from vbwd.events.payment_events import PaymentFailedEvent

        subscription = self._subscription_repo().find_by_provider_subscription_id(
            provider_subscription_id
        )
        if not subscription:
            return
        event = PaymentFailedEvent(
            subscription_id=subscription.id,
            user_id=subscription.user_id,
            error_code="payment_failed",
            error_message=error_message,
            provider=provider,
        )
        self._container().event_dispatcher().emit(event)

    def mark_invoice_payment_failed(
        self,
        invoice_id: UUID,
        provider: str,
        error_message: str,
        error_code: str = "payment_failed",
    ) -> None:
        from vbwd.models.enums import LineItemType
        from vbwd.events.payment_events import PaymentFailedEvent

        invoice = self._invoice_repo().find_by_id(invoice_id)
        if not invoice:
            return
        sub_repo = self._subscription_repo()
        for line_item in invoice.line_items:
            if line_item.item_type == LineItemType.SUBSCRIPTION:
                subscription = sub_repo.find_by_id(line_item.item_id)
                if subscription:
                    event = PaymentFailedEvent(
                        subscription_id=subscription.id,
                        user_id=subscription.user_id,
                        error_code=error_code,
                        error_message=error_message,
                        provider=provider,
                    )
                    self._container().event_dispatcher().emit(event)
                break
