"""S85.1 — TarifPlan and AddOn conform to the core ``Priceable`` protocol.

After the storage migration each sellable exposes ``raw_price`` (a float reading
the stored ``price``) and a uniform ``taxes`` relationship; the dropped
``currency`` / ``price_float`` columns no longer exist; and ``to_dict()`` no
longer carries those keys. No DB needed — these assert mapped attributes and the
serialisation shape.
"""
from uuid import uuid4

from vbwd.models.enums import BillingPeriod
from vbwd.pricing.priceable import Priceable
from plugins.subscription.subscription.models import AddOn, TarifPlan


def _plan() -> TarifPlan:
    plan = TarifPlan(
        name="Pro",
        slug="pro",
        price=100.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    plan.id = uuid4()
    plan.taxes = []
    return plan


def _addon() -> AddOn:
    addon = AddOn(name="Extra", slug="extra", price=9.99)
    addon.id = uuid4()
    addon.taxes = []
    return addon


def test_tarif_plan_raw_price_returns_stored_price_float():
    plan = _plan()
    assert plan.raw_price == 100.0
    assert isinstance(plan.raw_price, float)


def test_addon_raw_price_returns_stored_price_float():
    addon = _addon()
    assert addon.raw_price == 9.99
    assert isinstance(addon.raw_price, float)


def test_tarif_plan_has_no_currency_or_price_float_column():
    assert not hasattr(TarifPlan, "currency")
    assert not hasattr(TarifPlan, "price_float")


def test_addon_has_no_currency_column():
    assert not hasattr(AddOn, "currency")


def test_addon_has_taxes_relationship_assignable():
    addon = _addon()
    assert list(addon.taxes) == []
    addon.taxes = []
    assert hasattr(AddOn, "taxes")


def test_tarif_plan_has_taxes_relationship():
    assert hasattr(TarifPlan, "taxes")


def test_to_dict_drops_currency_and_price_float_keys():
    plan_dict = _plan().to_dict()
    assert "currency" not in plan_dict
    assert "price_float" not in plan_dict
    addon_dict = _addon().to_dict()
    assert "currency" not in addon_dict


def test_both_sellables_satisfy_priceable_protocol():
    assert isinstance(_plan(), Priceable)
    assert isinstance(_addon(), Priceable)
