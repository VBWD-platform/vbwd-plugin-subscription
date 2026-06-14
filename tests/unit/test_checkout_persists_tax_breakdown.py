"""S85.4 — subscription checkout persists the per-rate tax breakdown.

A checkout for a taxed plan records the line item's first-class ``net_amount`` /
``tax_amount`` / ``tax_breakdown`` columns (not just free-form metadata) and
rolls the invoice ``subtotal`` / ``tax_amount`` / ``total_amount`` up from the
lines. The charge total stays ``Price.brutto`` (D8). Flipping the global
``prices_mode_in_db`` changes the recorded net/tax for the same stored price.
"""
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from vbwd.models.tax import Tax
from vbwd.pricing.price_factory import PriceFactory
from plugins.subscription.subscription.events import CheckoutRequestedEvent
from plugins.subscription.subscription.handlers.checkout_handler import CheckoutHandler
from plugins.subscription.subscription.models.tarif_plan import TarifPlan


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
    plan = TarifPlan(name="Pro", slug="pro", price=price)
    plan.id = uuid4()
    plan.is_active = True
    plan.trial_days = 0
    plan.categories = []
    plan.taxes = taxes
    return plan


def _captured_line_items(saved):
    """The InvoiceLineItem objects passed to invoice_line_item.create()."""
    return [call.args[0] for call in saved.create.call_args_list]


def _run_checkout(prices_mode_in_db, plan):
    container = MagicMock()
    container.price_factory.return_value = _price_factory(prices_mode_in_db)

    repos = {
        "subscription": MagicMock(),
        "tarif_plan": MagicMock(),
        "tarif_plan_category": MagicMock(),
        "token_bundle": MagicMock(),
        "token_bundle_purchase": MagicMock(),
        "addon": MagicMock(),
        "addon_subscription": MagicMock(),
        "invoice": MagicMock(),
        "invoice_line_item": MagicMock(),
    }
    repos["tarif_plan"].find_by_id.return_value = plan

    # The invoice the handler saves; expose it back via find_by_id so the
    # handler's reload + to_dict path works.
    saved_invoices = []

    def _save_invoice(invoice):
        saved_invoices.append(invoice)

    repos["invoice"].save.side_effect = _save_invoice
    repos["invoice"].find_by_id.side_effect = lambda _id: saved_invoices[-1]

    handler = CheckoutHandler(container)
    handler._get_repos = lambda: repos  # type: ignore[method-assign]

    event = CheckoutRequestedEvent(
        user_id=uuid4(),
        plan_id=plan.id,
        currency="EUR",
        payment_method_code="stripe",
    )
    result = handler.handle(event)
    assert result.success, result.error
    return repos, saved_invoices[-1]


def test_netto_mode_records_line_tax_fields_and_rolls_up_invoice():
    plan = _plan(100.0, [_tax(19)])
    repos, invoice = _run_checkout("NETTO", plan)

    lines = _captured_line_items(repos["invoice_line_item"])
    subscription_line = next(line for line in lines if line.description == "Pro")
    assert subscription_line.net_amount == Decimal("100.00")
    assert subscription_line.tax_amount == Decimal("19.00")
    assert subscription_line.total_price == Decimal("119.00")  # gross unchanged
    assert subscription_line.tax_breakdown == [
        {"code": "VAT_DE", "name": "VAT", "rate": 19.0, "amount": 19.0}
    ]

    assert invoice.subtotal == Decimal("100.00")
    assert invoice.tax_amount == Decimal("19.00")
    assert invoice.total_amount == Decimal("119.00")


def test_brutto_mode_changes_recorded_net_and_tax_for_same_stored_price():
    plan = _plan(119.0, [_tax(19)])
    repos, invoice = _run_checkout("BRUTTO", plan)

    lines = _captured_line_items(repos["invoice_line_item"])
    subscription_line = next(line for line in lines if line.description == "Pro")
    assert subscription_line.net_amount == Decimal("100.00")
    assert subscription_line.tax_amount == Decimal("19.00")
    assert subscription_line.total_price == Decimal("119.00")  # gross == charge


def test_taxless_plan_records_net_equals_gross_empty_breakdown():
    plan = _plan(50.0, [])
    repos, invoice = _run_checkout("NETTO", plan)

    lines = _captured_line_items(repos["invoice_line_item"])
    subscription_line = next(line for line in lines if line.description == "Pro")
    assert subscription_line.net_amount == Decimal("50.00")
    assert subscription_line.tax_amount == Decimal("0.00")
    assert subscription_line.tax_breakdown == []
    assert invoice.tax_amount == Decimal("0.00")
    assert invoice.subtotal == invoice.total_amount == Decimal("50.00")
