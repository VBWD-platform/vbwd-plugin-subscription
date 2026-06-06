"""S50.4 — subscription plugin wires the recurring-billing bus subscribers.

``register_event_handlers(bus)`` (the BasePlugin hook) must subscribe the five
domain-neutral recurring-billing facts that payment plugins publish, so a
checkout/renewal/cancel/fail performed by stripe/paypal/yookassa reaches the
subscription domain. Without this wiring every published fact is a silent no-op.
"""
from unittest.mock import MagicMock

from plugins.subscription import SubscriptionPlugin


def test_register_event_handlers_subscribes_recurring_billing_facts():
    bus = MagicMock()
    SubscriptionPlugin().register_event_handlers(bus)

    subscribed = {call.args[0] for call in bus.subscribe.call_args_list}
    assert {
        "payment.provider_linked",
        "payment.recurring_charge",
        "payment.provider_cancelled",
        "payment.recurring_failed",
        "payment.invoice_failed",
    } <= subscribed
