"""S85.4 gap #3 — the dashboard active-subscription plan carries the net/gross split.

``GET /api/v1/user/subscriptions/active`` enriches ``subscription.plan`` with the
computed ``Price`` split (net/gross/taxes) + the display-mode pair so the
dashboard subscription-details view applies the business overlay and shows the
tax disclosure, instead of falling back to the bare gross ``price``.
"""
from unittest.mock import MagicMock

from vbwd.pricing.price_factory import PriceFactory
from plugins.subscription.subscription.routes.user_subscriptions import (
    _plan_summary_with_pricing,
)


class _FakeTax:
    def __init__(self, code, rate):
        self.code = code
        self.rate = rate


class _FakeBillingPeriod:
    value = "monthly"


class _FakePlan:
    def __init__(self, price, taxes, price_display_mode=None):
        self.id = "plan-uuid"
        self.name = "Pro"
        self.slug = "pro"
        self.price = price
        self.raw_price = price
        self.taxes = taxes
        self.billing_period = _FakeBillingPeriod()
        self.price_display_mode = price_display_mode


def _factory(prices_mode_in_db="NETTO"):
    settings_reader = MagicMock(return_value={"prices_mode_in_db": prices_mode_in_db})
    currency_service = MagicMock()
    currency_service.get_default_currency.return_value = MagicMock(code="EUR")
    return PriceFactory(
        settings_reader=settings_reader, currency_service=currency_service
    )


def _build(app, plan, prices_mode_in_db="NETTO"):
    with app.app_context():
        return _plan_summary_with_pricing(plan, _factory(prices_mode_in_db))


def test_plan_summary_carries_price_split_and_display_mode(app):
    summary = _build(app, _FakePlan(100.0, [_FakeTax("VAT_DE", 19.0)]))

    assert summary["net_price"] == 100.0
    assert summary["gross_price"] == 119.0
    assert summary["currency"] == "EUR"
    assert summary["price_obj"]["brutto"] == 119.0
    assert summary["price"] == 100.0  # backward-compatible bare number
    assert "effective_display_mode" in summary
    assert "prices_display_mode" in summary


def test_mode_flip_changes_net_gross_for_same_double(app):
    plan = _FakePlan(100.0, [_FakeTax("VAT_DE", 19.0)])
    netto_summary = _build(app, plan, "NETTO")
    brutto_summary = _build(app, plan, "BRUTTO")

    assert netto_summary["net_price"] != brutto_summary["net_price"]


def test_taxless_plan_has_equal_net_and_gross(app):
    summary = _build(app, _FakePlan(50.0, []))

    assert summary["net_price"] == summary["gross_price"] == 50.0
    assert summary["price_obj"]["taxes"] == []


def test_item_override_drives_effective_mode(app):
    summary = _build(
        app, _FakePlan(100.0, [_FakeTax("VAT_DE", 19.0)], price_display_mode="netto")
    )

    assert summary["effective_display_mode"] == "netto"
