"""S138.0 Inc 3 — a plan's default tokens are credited through TokenService.

``_credit_plan_default_tokens`` used to mutate ``UserTokenBalance`` and append
the ``TokenTransaction`` itself through the repositories, bypassing core's
``TokenService``. So activating a subscription moved tokens that no
token-movement hook could observe, and the balance and its ledger row were not
atomic with each other.

The path had no test at all before this increment.
"""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from vbwd.events.line_item_registry import LineItemContext
from vbwd.models.enums import LineItemType, SubscriptionStatus, TokenTransactionType

from plugins.subscription.subscription.handlers.line_item_handler import (
    SubscriptionLineItemHandler,
)

DEFAULT_TOKENS = 400
PLAN_NAME = "Pro"


@pytest.fixture()
def token_service():
    return MagicMock()


@pytest.fixture()
def subscription():
    plan = MagicMock()
    plan.name = PLAN_NAME
    plan.features = {"default_tokens": DEFAULT_TOKENS}
    plan.categories = []
    plan.billing_period = MagicMock()
    pending_subscription = MagicMock()
    pending_subscription.id = uuid4()
    pending_subscription.status = SubscriptionStatus.PENDING
    pending_subscription.tarif_plan = plan
    return pending_subscription


@pytest.fixture()
def container(token_service, subscription):
    mock_container = MagicMock()
    mock_container.token_service.return_value = token_service
    mock_container.subscription_repository.return_value.find_by_id.return_value = (
        subscription
    )
    return mock_container


@pytest.fixture()
def context(container):
    invoice = MagicMock()
    invoice.user_id = uuid4()
    return LineItemContext(
        invoice=invoice, user_id=invoice.user_id, container=container
    )


@pytest.fixture(autouse=True)
def _silence_lifecycle_events():
    """The event publish is not this test's subject (and needs an app context)."""
    with patch.object(SubscriptionLineItemHandler, "_publish_subscription_event"):
        yield


def _subscription_line_item():
    line_item = MagicMock()
    line_item.item_type = LineItemType.SUBSCRIPTION
    line_item.item_id = uuid4()
    return line_item


class TestPlanDefaultTokensRouteThroughTokenService:
    def test_credits_through_the_service(
        self, container, context, token_service, subscription
    ):
        handler = SubscriptionLineItemHandler(container)

        result = handler.activate_line_item(_subscription_line_item(), context)

        assert result.success is True
        assert result.data["tokens_credited"] == DEFAULT_TOKENS
        token_service.credit_tokens.assert_called_once()
        call = token_service.credit_tokens.call_args.kwargs
        assert call["user_id"] == context.user_id
        assert call["amount"] == DEFAULT_TOKENS
        assert call["transaction_type"] == TokenTransactionType.SUBSCRIPTION
        assert call["reference_id"] == subscription.id
        assert call["description"] == f"Plan tokens: {PLAN_NAME}"

    def test_never_writes_the_balance_repository_directly(self, container, context):
        handler = SubscriptionLineItemHandler(container)

        handler.activate_line_item(_subscription_line_item(), context)

        container.token_balance_repository.assert_not_called()
        container.token_transaction_repository.assert_not_called()

    def test_a_plan_without_default_tokens_credits_nothing(
        self, container, context, token_service, subscription
    ):
        subscription.tarif_plan.features = {}
        handler = SubscriptionLineItemHandler(container)

        result = handler.activate_line_item(_subscription_line_item(), context)

        assert result.data["tokens_credited"] == 0
        token_service.credit_tokens.assert_not_called()

    def test_a_plan_with_non_dict_features_credits_nothing(
        self, container, context, token_service, subscription
    ):
        subscription.tarif_plan.features = None
        handler = SubscriptionLineItemHandler(container)

        result = handler.activate_line_item(_subscription_line_item(), context)

        assert result.data["tokens_credited"] == 0
        token_service.credit_tokens.assert_not_called()
