"""S85.1a — ``TarifPlan`` no longer depends on the dead persisted ``Price``.

Characterisation of the teardown: the only runtime read path that touched the
persisted ``Price`` was ``TarifPlan.to_dict()`` reading the ``price_obj``
relationship. After S85.1a the ``price_id`` FK column and the ``price_obj``
relationship are removed; ``to_dict()`` derives the price block from the legacy
``price``/``currency`` doubles that remain the source of truth until S85.2.

No DB needed — these assert the mapped attributes and the serialisation shape.
"""
from uuid import uuid4

from vbwd.models.enums import BillingPeriod
from plugins.subscription.subscription.models import TarifPlan


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


def test_tarif_plan_has_no_price_id_column():
    assert not hasattr(
        TarifPlan, "price_id"
    ), "TarifPlan.price_id (FK to the dead vbwd_price) must be removed (S85.1a)."


def test_tarif_plan_has_no_price_obj_relationship():
    assert not hasattr(TarifPlan, "price_obj"), (
        "TarifPlan.price_obj relationship to the dead Price model must be "
        "removed (S85.1a)."
    )


def test_to_dict_serialises_raw_price_float():
    result = _plan().to_dict()
    # S85.1 (D5): a single ``price`` double, no nested currency block.
    assert result["price"] == 100.0
