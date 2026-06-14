"""S85.2 — ``TarifPlanService.get_plan_with_pricing`` computes via ``PriceFactory``.

The assigned-tax breakdown is delegated to the core ``PriceFactory`` (D1): the
same stored plan-price double yields different net/gross when the global
``prices_mode_in_db`` flips. The result also carries the serialized ``price``
object (``Price.to_dict()``) for the unified consumer.
"""
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from vbwd.models.tax import Tax
from vbwd.pricing.price_factory import PriceFactory
from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from plugins.subscription.subscription.services.tarif_plan_service import (
    TarifPlanService,
)


def _factory(prices_mode_in_db):
    settings_reader = MagicMock(return_value={"prices_mode_in_db": prices_mode_in_db})
    currency_service = MagicMock()
    currency_service.get_default_currency.return_value = MagicMock(code="EUR")
    return PriceFactory(
        settings_reader=settings_reader, currency_service=currency_service
    )


def _service(prices_mode_in_db):
    currency_service = MagicMock()
    currency_service.get_currency_by_code.return_value = MagicMock(code="EUR")
    return TarifPlanService(
        tarif_plan_repo=MagicMock(),
        currency_service=currency_service,
        tax_service=MagicMock(),
        price_factory=_factory(prices_mode_in_db),
    )


def _plan(price, taxes):
    plan = TarifPlan(name="Pro", slug="pro", price=price)
    plan.id = uuid4()
    plan.taxes = taxes
    return plan


def _tax(rate):
    tax = Tax(name="VAT", code="VAT_DE", rate=Decimal(str(rate)))
    tax.id = uuid4()
    return tax


def test_netto_mode_adds_tax_on_top():
    result = _service("NETTO").get_plan_with_pricing(
        _plan(100.0, [_tax(19)]), currency_code="EUR"
    )
    assert result["net_amount"] == Decimal("100.00")
    assert result["gross_amount"] == Decimal("119.00")


def test_brutto_mode_extracts_net_from_gross():
    result = _service("BRUTTO").get_plan_with_pricing(
        _plan(119.0, [_tax(19)]), currency_code="EUR"
    )
    assert result["gross_amount"] == Decimal("119.00")
    assert result["net_amount"] == Decimal("100.00")


def test_mode_flip_changes_net_and_gross_for_same_double():
    plan = _plan(100.0, [_tax(19)])
    netto = _service("NETTO").get_plan_with_pricing(plan, currency_code="EUR")
    brutto = _service("BRUTTO").get_plan_with_pricing(plan, currency_code="EUR")
    assert netto["gross_amount"] != brutto["gross_amount"]
    assert netto["net_amount"] != brutto["net_amount"]


def test_result_embeds_serialized_price_object():
    result = _service("NETTO").get_plan_with_pricing(
        _plan(100.0, [_tax(19)]), currency_code="EUR"
    )
    assert result["price"]["netto"] == 100.0
    assert result["price"]["brutto"] == 119.0
    assert result["price"]["currency"] == "EUR"
