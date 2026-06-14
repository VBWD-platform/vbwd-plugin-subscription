"""S85.4 gap #3 — the public add-on payload carries the display-mode pair.

The add-ons surfaces (catalog list + detail) need ``effective_display_mode``
(+ the global ``prices_display_mode``) alongside the computed ``price_info`` so
the business-viewer overlay and the per-item override apply, instead of
defaulting to the global/brutto side.
"""
from unittest.mock import MagicMock

from vbwd.pricing.price_factory import PriceFactory
from plugins.subscription.subscription.routes.user_addons import (
    _addon_dict_with_price,
)


class _FakeTax:
    def __init__(self, code, rate):
        self.code = code
        self.rate = rate


class _FakeAddon:
    def __init__(self, price, taxes, price_display_mode=None):
        self.raw_price = price
        self.taxes = taxes
        self.name = "Extra seats"
        self.price_display_mode = price_display_mode

    def to_dict(self):
        return {"name": self.name, "price": self.raw_price}


def _factory():
    settings_reader = MagicMock(return_value={"prices_mode_in_db": "NETTO"})
    currency_service = MagicMock()
    currency_service.get_default_currency.return_value = MagicMock(code="EUR")
    return PriceFactory(
        settings_reader=settings_reader, currency_service=currency_service
    )


def _call(app, addon):
    with app.app_context():
        app.container.price_factory = MagicMock(return_value=_factory())
        return _addon_dict_with_price(addon)


def test_payload_keeps_price_info_and_adds_display_mode(app):
    result = _call(app, _FakeAddon(100.0, [_FakeTax("VAT_DE", 19.0)]))
    assert result["price_info"]["price"]["brutto"] == 119.0
    assert "prices_display_mode" in result
    assert "effective_display_mode" in result


def test_item_override_drives_effective_mode(app):
    result = _call(
        app,
        _FakeAddon(100.0, [_FakeTax("VAT_DE", 19.0)], price_display_mode="netto"),
    )
    assert result["effective_display_mode"] == "netto"
