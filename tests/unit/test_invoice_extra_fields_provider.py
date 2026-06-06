"""S50.3 — the subscription plugin contributes invoice extra fields.

The plugin registers a provider against the core generic
``invoice_extra_fields_registry`` whose callback returns ``enrich_invoice``'s
body. This test pins the keys the provider yields for an invoice linked to a
subscription, with the repositories mocked (no DB).
"""
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from vbwd.models.enums import LineItemType, SubscriptionStatus, BillingPeriod
from vbwd.services.invoice_extra_fields_registry import (
    aggregate_invoice_extra_fields,
    clear_invoice_extra_fields_providers,
    register_invoice_extra_fields_provider,
)
from plugins.subscription.subscription.services.subscription_read_model import (
    SubscriptionReadModel,
)


@pytest.fixture(autouse=True)
def _isolate():
    clear_invoice_extra_fields_providers()
    yield
    clear_invoice_extra_fields_providers()


def _invoice_with_subscription_line_item(subscription_id):
    line_item = MagicMock()
    line_item.item_type = LineItemType.SUBSCRIPTION
    line_item.item_id = subscription_id
    invoice = MagicMock()
    invoice.line_items = [line_item]
    return invoice


def _subscription_with_plan():
    plan = MagicMock()
    plan.name = "Pro"
    plan.description = "Pro plan"
    plan.billing_period = BillingPeriod.MONTHLY
    plan.price = Decimal("9.99")

    subscription = MagicMock()
    subscription.tarif_plan = plan
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.started_at = None
    subscription.expires_at = None
    subscription.trial_end_at = None
    return subscription


def test_enrich_invoice_yields_plan_and_subscription_keys():
    subscription_id = uuid4()
    invoice = _invoice_with_subscription_line_item(subscription_id)
    subscription = _subscription_with_plan()

    read_model = SubscriptionReadModel()
    subscription_repo = MagicMock()
    subscription_repo.find_by_id.return_value = subscription
    read_model._subscription_repo = lambda: subscription_repo

    enrichment = read_model.enrich_invoice(invoice)

    assert enrichment["plan_name"] == "Pro"
    assert enrichment["plan_price"] == "9.99"
    assert enrichment["subscription_status"] == "ACTIVE"
    assert set(enrichment) == {
        "plan_name",
        "plan_description",
        "plan_billing_period",
        "plan_price",
        "subscription_status",
        "subscription_start_date",
        "subscription_end_date",
        "subscription_is_trial",
        "subscription_trial_end",
    }


def test_registered_provider_merges_enrich_invoice_keys():
    subscription_id = uuid4()
    invoice = _invoice_with_subscription_line_item(subscription_id)
    subscription = _subscription_with_plan()

    read_model = SubscriptionReadModel()
    subscription_repo = MagicMock()
    subscription_repo.find_by_id.return_value = subscription
    read_model._subscription_repo = lambda: subscription_repo

    register_invoice_extra_fields_provider(
        "subscription", lambda inv: read_model.enrich_invoice(inv)
    )

    merged = aggregate_invoice_extra_fields(invoice)
    assert merged["plan_name"] == "Pro"
    assert merged["subscription_status"] == "ACTIVE"


def test_enrich_invoice_with_no_subscription_line_item_is_empty():
    invoice = MagicMock()
    invoice.line_items = []
    assert SubscriptionReadModel().enrich_invoice(invoice) == {}
