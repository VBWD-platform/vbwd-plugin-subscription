"""S72.3 — tax assignment on TarifPlan (unit, no DB).

RED→GREEN contract:
- ``TarifPlan.to_dict()`` exposes ``tax_ids: [<id>]`` and resolved
  ``taxes: [{id, code, name, rate}]`` from the M2M ``taxes`` relationship.
- ``TarifPlanService.get_plan_with_pricing`` sums the rates of the *assigned*
  taxes into net/tax/gross when taxes are present (assigned taxes take
  precedence over the country-based breakdown fallback).
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from vbwd.models.enums import BillingPeriod
from vbwd.models.tax import Tax
from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.services.tarif_plan_service import (
    TarifPlanService,
)


def _fake_tax(code: str, name: str, rate: str) -> Tax:
    """A real core ``Tax`` instance (no DB) — exercises ``calculate``."""
    tax = Tax(name=name, code=code, rate=Decimal(rate))
    tax.id = uuid4()
    return tax


def _plan(price: float = 100.0, taxes=None) -> TarifPlan:
    plan = TarifPlan(
        name="Pro",
        slug="pro",
        price=price,
        billing_period=BillingPeriod.MONTHLY,
    )
    plan.id = uuid4()
    # The relationship is normally lazy-loaded; in-memory we set it directly.
    plan.taxes = taxes or []
    return plan


def test_to_dict_exposes_tax_ids_and_resolved_taxes():
    vat = _fake_tax("VAT_DE", "German VAT", "19.00")
    reduced = _fake_tax("VAT_DE_RED", "German VAT (reduced)", "7.00")
    plan = _plan(taxes=[vat, reduced])

    data = plan.to_dict()

    assert data["tax_ids"] == [str(vat.id), str(reduced.id)]
    assert data["taxes"] == [
        {"id": str(vat.id), "code": "VAT_DE", "name": "German VAT", "rate": "19.00"},
        {
            "id": str(reduced.id),
            "code": "VAT_DE_RED",
            "name": "German VAT (reduced)",
            "rate": "7.00",
        },
    ]


def test_to_dict_no_taxes_yields_empty_lists():
    plan = _plan(taxes=[])

    data = plan.to_dict()

    assert data["tax_ids"] == []
    assert data["taxes"] == []


def test_pricing_sums_assigned_tax_rates_into_net_tax_gross():
    """Assigned taxes (19% + 7% = 26%) take precedence; net=price, tax=26,
    gross=126 on a 100.00 plan."""
    currency_service = MagicMock()
    currency_service.get_currency_by_code.return_value = SimpleNamespace(code="EUR")
    plan = _plan(
        price=100.0,
        taxes=[
            _fake_tax("VAT_DE", "German VAT", "19.00"),
            _fake_tax("VAT_DE_RED", "German VAT (reduced)", "7.00"),
        ],
    )

    service = TarifPlanService(
        tarif_plan_repo=MagicMock(),
        currency_service=currency_service,
        tax_service=MagicMock(),
    )

    result = service.get_plan_with_pricing(plan, currency_code="EUR")

    assert result["net_amount"] == Decimal("100.00")
    assert result["tax_amount"] == Decimal("26.00")
    assert result["gross_amount"] == Decimal("126.00")
    assert result["tax_rate"] == Decimal("26.00")
    assert [t["code"] for t in result["taxes"]] == ["VAT_DE", "VAT_DE_RED"]


def test_pricing_falls_back_to_country_breakdown_when_no_taxes_assigned():
    """With no assigned taxes the existing country-based breakdown is used."""
    currency_service = MagicMock()
    currency_service.get_currency_by_code.return_value = SimpleNamespace(code="EUR")
    tax_service = MagicMock()
    tax_service.get_tax_breakdown.return_value = {
        "net_amount": Decimal("100.00"),
        "tax_amount": Decimal("19.00"),
        "gross_amount": Decimal("119.00"),
        "tax_rate": Decimal("19.00"),
    }
    plan = _plan(price=100.0, taxes=[])

    service = TarifPlanService(
        tarif_plan_repo=MagicMock(),
        currency_service=currency_service,
        tax_service=tax_service,
    )

    result = service.get_plan_with_pricing(plan, currency_code="EUR", country_code="DE")

    tax_service.get_tax_breakdown.assert_called_once()
    assert result["gross_price"] == Decimal("119.00")
    assert result["tax_rate"] == Decimal("19.00")
    # No assigned-tax keys when falling back.
    assert "gross_amount" not in result
