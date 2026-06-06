"""Unit tests for the recurring-billing bus subscribers (S50.4).

The subscription plugin subscribes to the domain-neutral facts that payment
plugins publish and performs link / renew / cancel / fail inline. These tests
mock the repos and assert the subscriber's effect, including that the renewal
subscriber creates the invoice AND re-emits ``payment.captured`` with the
forwarded metadata so downstream capture handling is preserved.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from vbwd.models.enums import LineItemType
from plugins.subscription.subscription.handlers.recurring_billing_subscriber import (
    RecurringBillingSubscriber,
)


def _subscriber_with_repos(subscription_repo, invoice_repo):
    subscriber = RecurringBillingSubscriber()
    subscriber._subscription_repo = lambda: subscription_repo
    subscriber._invoice_repo = lambda: invoice_repo
    return subscriber


def test_subscribe_registers_all_five_event_names():
    bus = MagicMock()
    RecurringBillingSubscriber().subscribe(bus)
    subscribed = {call.args[0] for call in bus.subscribe.call_args_list}
    assert subscribed == {
        "payment.provider_linked",
        "payment.recurring_charge",
        "payment.provider_cancelled",
        "payment.recurring_failed",
        "payment.invoice_failed",
    }


def test_provider_linked_records_ref_id_on_subscription():
    subscription = SimpleNamespace(provider_subscription_id=None)
    line_item = SimpleNamespace(item_type=LineItemType.SUBSCRIPTION, item_id=uuid4())
    invoice = SimpleNamespace(line_items=[line_item])
    invoice_repo = MagicMock()
    invoice_repo.find_by_id.return_value = invoice
    subscription_repo = MagicMock()
    subscription_repo.find_by_id.return_value = subscription

    subscriber = _subscriber_with_repos(subscription_repo, invoice_repo)
    subscriber.on_provider_linked(
        "payment.provider_linked",
        {
            "invoice_id": str(uuid4()),
            "provider": "stripe",
            "provider_ref_id": "sub_ref_1",
        },
    )

    assert subscription.provider_subscription_id == "sub_ref_1"
    subscription_repo.save.assert_called_once_with(subscription)


def test_provider_cancelled_emits_subscription_cancelled():
    subscription = SimpleNamespace(id=uuid4(), user_id=uuid4())
    subscription_repo = MagicMock()
    subscription_repo.find_by_provider_subscription_id.return_value = subscription

    subscriber = _subscriber_with_repos(subscription_repo, MagicMock())
    emitted = []
    subscriber._emit = lambda event: emitted.append(event)

    subscriber.on_provider_cancelled(
        "payment.provider_cancelled",
        {"provider": "stripe", "provider_ref_id": "sub_ref_1", "reason": None},
    )

    assert len(emitted) == 1
    event = emitted[0]
    assert event.name == "subscription.cancelled"
    assert event.subscription_id == subscription.id
    assert event.reason == "stripe_subscription_cancelled"


def test_recurring_failed_flags_payment_failed_at_once():
    subscription = SimpleNamespace(payment_failed_at=None)
    subscription_repo = MagicMock()
    subscription_repo.find_by_provider_subscription_id.return_value = subscription

    subscriber = _subscriber_with_repos(subscription_repo, MagicMock())
    subscriber.on_recurring_failed(
        "payment.recurring_failed",
        {"provider": "stripe", "provider_ref_id": "sub_ref_1", "error_message": "x"},
    )

    assert subscription.payment_failed_at is not None
    subscription_repo.save.assert_called_once_with(subscription)


def test_recurring_failed_no_match_is_noop():
    subscription_repo = MagicMock()
    subscription_repo.find_by_provider_subscription_id.return_value = None
    subscriber = _subscriber_with_repos(subscription_repo, MagicMock())

    subscriber.on_recurring_failed(
        "payment.recurring_failed",
        {"provider": "stripe", "provider_ref_id": "missing", "error_message": "x"},
    )

    subscription_repo.save.assert_not_called()


def test_invoice_failed_flags_subscription_on_invoice():
    subscription = SimpleNamespace(payment_failed_at=None)
    line_item = SimpleNamespace(item_type=LineItemType.SUBSCRIPTION, item_id=uuid4())
    invoice = SimpleNamespace(line_items=[line_item])
    invoice_repo = MagicMock()
    invoice_repo.find_by_id.return_value = invoice
    subscription_repo = MagicMock()
    subscription_repo.find_by_id.return_value = subscription

    subscriber = _subscriber_with_repos(subscription_repo, invoice_repo)
    subscriber.on_invoice_failed(
        "payment.invoice_failed",
        {
            "invoice_id": str(uuid4()),
            "provider": "yookassa",
            "error_message": "canceled",
            "error_code": "payment_canceled",
        },
    )

    assert subscription.payment_failed_at is not None
    subscription_repo.save.assert_called_once_with(subscription)


@patch(
    "plugins.subscription.subscription.handlers.recurring_billing_subscriber."
    "emit_payment_captured"
)
def test_recurring_charge_creates_invoice_then_emits_captured_with_metadata(
    mock_emit_captured,
):
    """The renewal subscriber creates the invoice, then emits payment.captured
    forwarding the provider metadata verbatim (byte-for-byte preservation)."""
    renewal_invoice_id = uuid4()
    subscriber = RecurringBillingSubscriber()
    subscriber._create_renewal_invoice = MagicMock(return_value=renewal_invoice_id)

    metadata = {"stripe": {"invoice_id": "in_renew", "captured_amount": "9.99"}}
    subscriber.on_recurring_charge(
        "payment.recurring_charge",
        {
            "provider": "stripe",
            "provider_ref_id": "sub_ref_1",
            "amount": "9.99",
            "currency": "eur",
            "provider_reference": "in_renew",
            "transaction_id": "pi_renew",
            "metadata": metadata,
        },
    )

    subscriber._create_renewal_invoice.assert_called_once_with(
        provider="stripe",
        provider_ref_id="sub_ref_1",
        amount="9.99",
        currency="eur",
        provider_reference="in_renew",
    )
    mock_emit_captured.assert_called_once_with(
        invoice_id=renewal_invoice_id,
        payment_reference="in_renew",
        amount="9.99",
        currency="eur",
        provider="stripe",
        transaction_id="pi_renew",
        metadata=metadata,
    )


@patch(
    "plugins.subscription.subscription.handlers.recurring_billing_subscriber."
    "emit_payment_captured"
)
def test_recurring_charge_no_match_does_not_emit(mock_emit_captured):
    """No matching subscription / already processed → no renewal id → no emit."""
    subscriber = RecurringBillingSubscriber()
    subscriber._create_renewal_invoice = MagicMock(return_value=None)

    subscriber.on_recurring_charge(
        "payment.recurring_charge",
        {
            "provider": "stripe",
            "provider_ref_id": "missing",
            "amount": "9.99",
            "currency": "eur",
            "provider_reference": "in_dup",
            "transaction_id": "",
            "metadata": {},
        },
    )

    mock_emit_captured.assert_not_called()


def test_create_renewal_invoice_dedup_returns_existing():
    subscription = SimpleNamespace(
        id=uuid4(), user_id=uuid4(), tarif_plan=SimpleNamespace(name="Pro")
    )
    subscription_repo = MagicMock()
    subscription_repo.find_by_provider_subscription_id.return_value = subscription
    existing = SimpleNamespace(id=uuid4())
    invoice_repo = MagicMock()
    invoice_repo.find_by_provider_session_id.return_value = existing

    subscriber = _subscriber_with_repos(subscription_repo, invoice_repo)
    result = subscriber._create_renewal_invoice(
        provider="stripe",
        provider_ref_id="sub_ref_1",
        amount=Decimal("9.99"),
        currency="eur",
        provider_reference="in_dup",
    )

    assert result == existing.id
    invoice_repo.save.assert_not_called()
