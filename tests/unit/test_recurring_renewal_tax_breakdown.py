"""S85.4 — the recurring renewal invoice carries a derived tax split.

The provider's actual charged gross stays authoritative for a recurring charge
(D8) — it is what the line/invoice total records. But the per-rate tax split is
still derived from the plan's ``Price`` (via the core ``PriceFactory``) and
scaled to the charged gross, so the renewal invoice's tax disclosure is
populated. A plan with no taxes ⇒ net == gross, empty breakdown.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from vbwd.models.tax import Tax
from vbwd.pricing.price_factory import PriceFactory
from plugins.subscription.subscription.handlers.recurring_billing_subscriber import (
    RecurringBillingSubscriber,
)


def _price_factory(prices_mode_in_db):
    settings_reader = MagicMock(return_value={"prices_mode_in_db": prices_mode_in_db})
    currency_service = MagicMock()
    currency_service.get_default_currency.return_value = MagicMock(code="EUR")
    return PriceFactory(
        settings_reader=settings_reader, currency_service=currency_service
    )


def _tax(rate):
    tax = Tax(name="VAT", code="VAT_DE", rate=Decimal(str(rate)))
    tax.id = uuid4()
    return tax


def _plan(price, taxes):
    plan = SimpleNamespace(name="Pro", raw_price=float(price), taxes=taxes)
    return plan


def _subscriber(plan, price_factory):
    subscription = SimpleNamespace(id=uuid4(), user_id=uuid4(), tarif_plan=plan)
    subscription_repo = MagicMock()
    subscription_repo.find_by_provider_subscription_id.return_value = subscription
    invoice_repo = MagicMock()
    invoice_repo.find_by_provider_session_id.return_value = None

    saved = []
    invoice_repo.save.side_effect = lambda inv: saved.append(inv)

    subscriber = RecurringBillingSubscriber()
    subscriber._subscription_repo = lambda: subscription_repo
    subscriber._invoice_repo = lambda: invoice_repo
    subscriber._price_factory = lambda: price_factory
    return subscriber, saved


def _renew(subscriber, amount):
    subscriber._create_renewal_invoice(
        provider="stripe",
        provider_ref_id="sub_ref_1",
        amount=amount,
        currency="eur",
        provider_reference="evt_1",
    )


def test_renewal_derives_tax_split_from_plan_keeping_provider_gross():
    plan = _plan(Decimal("100.00"), [_tax(19)])
    subscriber, saved = _subscriber(plan, _price_factory("NETTO"))

    _renew(subscriber, "119.00")

    invoice = saved[0]
    assert invoice.total_amount == Decimal("119.00")  # provider gross authoritative
    assert invoice.subtotal == Decimal("100.00")
    assert invoice.tax_amount == Decimal("19.00")

    line = invoice.line_items[0]
    assert line.total_price == Decimal("119.00")
    assert line.net_amount == Decimal("100.00")
    assert line.tax_amount == Decimal("19.00")
    assert line.tax_breakdown[0]["code"] == "VAT_DE"


def test_renewal_taxless_plan_net_equals_gross_empty_breakdown():
    plan = _plan(Decimal("50.00"), [])
    subscriber, saved = _subscriber(plan, _price_factory("NETTO"))

    _renew(subscriber, "50.00")

    invoice = saved[0]
    line = invoice.line_items[0]
    assert line.net_amount == Decimal("50.00")
    assert line.tax_amount == Decimal("0.00")
    assert line.tax_breakdown == []
