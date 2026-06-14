"""S72.4 — per-plan netto/brutto price-display override (unit, no DB).

RED→GREEN contract:
- ``TarifPlan`` accepts ``price_display_mode`` (``None`` = inherit global,
  ``"netto"``/``"brutto"`` = override) on create/update and exposes it in
  ``to_dict()``.
- An invalid ``price_display_mode`` is rejected (validation raises ``ValueError``;
  the admin route turns that into 400).
- ``TarifPlanService.get_plan_with_pricing`` exposes
  ``effective_display_mode = override ?? global`` and the global
  ``prices_display_mode`` value itself (so the fe-user consumer can render the
  "netto price" tag, which fires when effective==netto AND global==brutto).
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from vbwd.models.enums import BillingPeriod
from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.models.tarif_plan import (
    validate_price_display_mode,
)
import vbwd.pricing.display_mode as display_mode_module
from plugins.subscription.subscription.services.tarif_plan_service import (
    TarifPlanService,
)


def _patch_global_mode(monkeypatch, mode: str) -> None:
    # S85.4: the display-mode pair is resolved by the single core helper
    # ``display_mode_fields`` (DRY) — patch its settings reader.
    monkeypatch.setattr(
        display_mode_module,
        "get_core_settings",
        lambda: {"prices_display_mode": mode},
    )


def _plan(price_display_mode=None) -> TarifPlan:
    plan = TarifPlan(
        name="Pro",
        slug="pro",
        price=100.0,
        billing_period=BillingPeriod.MONTHLY,
        price_display_mode=price_display_mode,
    )
    plan.id = uuid4()
    plan.taxes = []
    return plan


def test_validate_accepts_none_netto_brutto():
    assert validate_price_display_mode(None) is None
    assert validate_price_display_mode("netto") == "netto"
    assert validate_price_display_mode("brutto") == "brutto"


def test_validate_rejects_unknown_value():
    with pytest.raises(ValueError):
        validate_price_display_mode("gross")


def test_to_dict_exposes_price_display_mode_default_none():
    plan = _plan(price_display_mode=None)

    data = plan.to_dict()

    assert "price_display_mode" in data
    assert data["price_display_mode"] is None


def test_to_dict_exposes_price_display_mode_override():
    plan = _plan(price_display_mode="netto")

    data = plan.to_dict()

    assert data["price_display_mode"] == "netto"


def _service():
    currency_service = MagicMock()
    currency_service.get_currency_by_code.return_value = SimpleNamespace(code="EUR")
    return TarifPlanService(
        tarif_plan_repo=MagicMock(),
        currency_service=currency_service,
        tax_service=MagicMock(),
    )


def test_pricing_exposes_global_mode_and_effective_inherits_when_override_none(
    monkeypatch,
):
    """Override is None → effective == global; global value also surfaced."""
    _patch_global_mode(monkeypatch, "brutto")
    plan = _plan(price_display_mode=None)

    result = _service().get_plan_with_pricing(plan, currency_code="EUR")

    assert result["prices_display_mode"] == "brutto"
    assert result["effective_display_mode"] == "brutto"


def test_pricing_override_wins_over_global(monkeypatch):
    """Override 'netto' under a 'brutto' global → effective == 'netto'."""
    _patch_global_mode(monkeypatch, "brutto")
    plan = _plan(price_display_mode="netto")

    result = _service().get_plan_with_pricing(plan, currency_code="EUR")

    assert result["prices_display_mode"] == "brutto"
    assert result["effective_display_mode"] == "netto"


def test_pricing_effective_follows_global_netto_when_no_override(monkeypatch):
    _patch_global_mode(monkeypatch, "netto")
    plan = _plan(price_display_mode=None)

    result = _service().get_plan_with_pricing(plan, currency_code="EUR")

    assert result["prices_display_mode"] == "netto"
    assert result["effective_display_mode"] == "netto"


def test_pricing_display_mode_present_even_with_assigned_taxes(monkeypatch):
    """The assigned-tax breakdown path must still carry the display-mode keys."""
    from vbwd.models.tax import Tax

    _patch_global_mode(monkeypatch, "brutto")
    tax = Tax(name="German VAT", code="VAT_DE", rate=Decimal("19.00"))
    tax.id = uuid4()
    plan = _plan(price_display_mode="netto")
    plan.taxes = [tax]

    result = _service().get_plan_with_pricing(plan, currency_code="EUR")

    assert result["gross_amount"] == Decimal("119.00")
    assert result["prices_display_mode"] == "brutto"
    assert result["effective_display_mode"] == "netto"
