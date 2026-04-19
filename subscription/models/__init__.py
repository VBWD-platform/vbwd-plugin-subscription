"""Subscription plugin models.

During transition (04b): re-exports from core.
After core cleanup (04c): plugin owns these models directly.
"""
from vbwd.models.subscription import Subscription
from vbwd.models.tarif_plan import TarifPlan
from vbwd.models.addon import AddOn, addon_tarif_plans
from vbwd.models.addon_subscription import AddOnSubscription
from vbwd.models.tarif_plan_category import (
    TarifPlanCategory,
    tarif_plan_category_plans,
)

__all__ = [
    "Subscription",
    "TarifPlan",
    "AddOn",
    "addon_tarif_plans",
    "AddOnSubscription",
    "TarifPlanCategory",
    "tarif_plan_category_plans",
]
