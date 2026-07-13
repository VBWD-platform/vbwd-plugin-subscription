"""SubscriptionLineItemHandler recurring-billing spec (Sprint 11 / S1).

Proves how the subscription plugin classifies its own line items so payment
providers (stripe/paypal/yookassa) set up recurring charges correctly:

  * SUBSCRIPTION on a recurring plan → recurring. This is the mode **ghrm**
    uses: a ghrm software package is sold as a (recurring) tarif plan, so the
    purchase is a SUBSCRIPTION line item that providers bill repeatedly.
  * SUBSCRIPTION on a one-off plan → one-time.
  * ADD_ON keeps its prior behaviour (recurring iff the add-on is recurring).
"""
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from vbwd.models.enums import LineItemType
from vbwd.events.line_item_registry import RecurringBillingSpec
from plugins.subscription.subscription.handlers.line_item_handler import (
    SubscriptionLineItemHandler,
)


def _handler():
    return SubscriptionLineItemHandler(container=MagicMock())


def _line_item(item_type):
    return SimpleNamespace(item_type=item_type, item_id="item-1", quantity=1)


@patch("vbwd.extensions.db")
def test_ghrm_recurring_plan_subscription_is_recurring(mock_db):
    """ghrm sells recurring tarif plans → SUBSCRIPTION line item → recurring."""
    plan = SimpleNamespace(
        name="GHRM Backend",
        is_recurring=True,
        billing_period=SimpleNamespace(value="MONTHLY"),
        trial_days=0,
    )
    mock_db.session.get.return_value = SimpleNamespace(tarif_plan=plan)
    handler = _handler()
    item = _line_item(LineItemType.SUBSCRIPTION)

    assert handler.is_recurring_line_item(item) is True
    assert handler.recurring_billing_spec(item) == RecurringBillingSpec(
        name="GHRM Backend", billing_period="MONTHLY"
    )


@patch("vbwd.extensions.db")
def test_subscription_spec_carries_plan_trial_days(mock_db):
    """A plan with a trial period forwards trial_days so the provider defers cycle 1."""
    plan = SimpleNamespace(
        name="Trial Plan",
        is_recurring=True,
        billing_period=SimpleNamespace(value="MONTHLY"),
        trial_days=14,
    )
    mock_db.session.get.return_value = SimpleNamespace(tarif_plan=plan)
    handler = _handler()
    item = _line_item(LineItemType.SUBSCRIPTION)

    spec = handler.recurring_billing_spec(item)
    assert spec == RecurringBillingSpec(
        name="Trial Plan", billing_period="MONTHLY", trial_days=14
    )
    assert spec.trial_days == 14


@patch("vbwd.extensions.db")
def test_addon_spec_defaults_trial_days_to_zero(mock_db):
    """Add-ons have no trial field → trial_days defaults to 0."""
    addon = SimpleNamespace(
        name="Priority Support", is_recurring=True, billing_period="MONTHLY"
    )
    mock_db.session.get.return_value = SimpleNamespace(addon=addon)
    handler = _handler()
    item = _line_item(LineItemType.ADD_ON)

    spec = handler.recurring_billing_spec(item)
    assert spec.trial_days == 0


@patch("vbwd.extensions.db")
def test_one_off_plan_subscription_is_not_recurring(mock_db):
    plan = SimpleNamespace(
        name="Lifetime",
        is_recurring=False,
        billing_period=SimpleNamespace(value="ONE_TIME"),
    )
    mock_db.session.get.return_value = SimpleNamespace(tarif_plan=plan)
    handler = _handler()
    item = _line_item(LineItemType.SUBSCRIPTION)

    assert handler.is_recurring_line_item(item) is False
    assert handler.recurring_billing_spec(item) is None


@patch("vbwd.extensions.db")
def test_recurring_addon_is_recurring(mock_db):
    addon = SimpleNamespace(
        name="Priority Support", is_recurring=True, billing_period="MONTHLY"
    )
    mock_db.session.get.return_value = SimpleNamespace(addon=addon)
    handler = _handler()
    item = _line_item(LineItemType.ADD_ON)

    assert handler.recurring_billing_spec(item) == RecurringBillingSpec(
        name="Priority Support", billing_period="MONTHLY"
    )


@patch("vbwd.extensions.db")
def test_one_time_addon_is_not_recurring(mock_db):
    addon = SimpleNamespace(
        name="Setup Fee", is_recurring=False, billing_period="ONE_TIME"
    )
    mock_db.session.get.return_value = SimpleNamespace(addon=addon)
    handler = _handler()
    item = _line_item(LineItemType.ADD_ON)

    assert handler.recurring_billing_spec(item) is None
