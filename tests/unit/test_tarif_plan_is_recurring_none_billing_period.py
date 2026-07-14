"""Regression: a TarifPlan with no billing period must NOT be recurring.

Production trial-subscription checkout failed at Stripe with
``Subscriptions require at least one recurring price or plan``. Root cause:
``TarifPlan.is_recurring`` returned ``True`` when ``billing_period`` was
``None`` (because ``None != BillingPeriod.ONE_TIME``). The payment mode-check
(``is_recurring_line_item``) then chose ``mode="subscription"`` while the
billing-spec builder (``recurring_billing_spec``) dereferenced
``None.value`` and raised — an exception the line-item registry swallows —
yielding zero Stripe subscription line_items. The two decisions diverged and
Stripe rejected the empty subscription.

These assert the invariant at its source: a spec-less plan is not recurring,
so the mode-check and the spec-builder can never disagree.
"""
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from uuid import uuid4

from vbwd.models.enums import BillingPeriod, LineItemType
from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.handlers.line_item_handler import (
    SubscriptionLineItemHandler,
)


def _plan(billing_period) -> TarifPlan:
    plan = TarifPlan(
        name="Pro",
        slug="pro",
        price=9.99,
        billing_period=billing_period,
    )
    plan.id = uuid4()
    return plan


def test_is_recurring_false_when_billing_period_none():
    assert _plan(None).is_recurring is False


def test_is_recurring_true_for_monthly():
    assert _plan(BillingPeriod.MONTHLY).is_recurring is True


def test_is_recurring_false_for_one_time():
    assert _plan(BillingPeriod.ONE_TIME).is_recurring is False


@patch("vbwd.extensions.db")
def test_none_billing_period_has_no_mode_spec_divergence(mock_db):
    """The mode-check and the spec-builder must AGREE for the real handler.

    A subscription on a plan whose ``billing_period`` is ``None`` must be
    reported not-recurring, so the swallowed-exception divergence
    (``mode=subscription`` with no billing spec) can never arise at its source.
    """
    plan = _plan(None)
    mock_db.session.get.return_value = SimpleNamespace(tarif_plan=plan)
    handler = SubscriptionLineItemHandler(container=MagicMock())
    item = SimpleNamespace(
        item_type=LineItemType.SUBSCRIPTION, item_id="item-1", quantity=1
    )

    assert handler.is_recurring_line_item(item) is False
    assert handler.recurring_billing_spec(item) is None
