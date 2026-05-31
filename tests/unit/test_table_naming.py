"""Oracle: subscription tables are `subscription_`-prefixed (sprint S43.4)."""
import pytest

from plugins.subscription.subscription.models.subscription import Subscription
from plugins.subscription.subscription.models.addon import AddOn
from plugins.subscription.subscription.models.addon_subscription import (
    AddOnSubscription,
)
from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from plugins.subscription.subscription.models.tarif_plan_category import (
    TarifPlanCategory,
)


@pytest.mark.parametrize(
    "model, expected",
    [
        (Subscription, "subscription_record"),
        (AddOn, "subscription_addon"),
        (AddOnSubscription, "subscription_addon_subscription"),
        (TarifPlan, "subscription_tarif_plan"),
        (TarifPlanCategory, "subscription_tarif_plan_category"),
    ],
)
def test_subscription_table_is_plugin_prefixed(model, expected):
    assert model.__tablename__ == expected
    assert model.__tablename__.startswith("subscription_")
