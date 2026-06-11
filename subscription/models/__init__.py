"""Subscription plugin models.

The plugin owns these model classes directly (Sprint 11 / S5). Core defines
none of them — the subscription↔invoice link is the invoice's SUBSCRIPTION
line item, not a core FK.
"""
from plugins.subscription.subscription.models.subscription import Subscription
from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from plugins.subscription.subscription.models.addon import AddOn, addon_tarif_plans
from plugins.subscription.subscription.models.addon_subscription import (
    AddOnSubscription,
)
from plugins.subscription.subscription.models.tarif_plan_category import (
    TarifPlanCategory,
    tarif_plan_category_plans,
)
from plugins.subscription.subscription.models.bot_checkout_draft import (
    BotCheckoutDraft,
)

__all__ = [
    "Subscription",
    "TarifPlan",
    "AddOn",
    "addon_tarif_plans",
    "AddOnSubscription",
    "TarifPlanCategory",
    "tarif_plan_category_plans",
    "BotCheckoutDraft",
]
