"""Unit tests for SubscriptionLineItemHandler (Sprint 04b)."""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from vbwd.events.line_item_registry import LineItemContext
from plugins.subscription.subscription.handlers.line_item_handler import (
    SubscriptionLineItemHandler,
)
from vbwd.models.enums import (
    LineItemType,
    SubscriptionStatus,
    TokenTransactionType,
)


@pytest.fixture()
def container():
    mock_container = MagicMock()
    mock_container.subscription_repository.return_value = MagicMock()
    mock_container.addon_subscription_repository.return_value = MagicMock()
    mock_container.token_balance_repository.return_value = MagicMock()
    mock_container.token_transaction_repository.return_value = MagicMock()
    mock_container.token_service.return_value = MagicMock()
    return mock_container


@pytest.fixture()
def handler(container):
    return SubscriptionLineItemHandler(container)


@pytest.fixture()
def context(container):
    invoice = MagicMock()
    invoice.user_id = uuid4()
    return LineItemContext(
        invoice=invoice, user_id=invoice.user_id, container=container
    )


def _make_line_item(item_type, item_id=None):
    line_item = MagicMock()
    line_item.item_type = item_type
    line_item.item_id = item_id or uuid4()
    return line_item


class TestCanHandle:
    def test_handles_subscription(self, handler, context):
        assert (
            handler.can_handle_line_item(
                _make_line_item(LineItemType.SUBSCRIPTION), context
            )
            is True
        )

    def test_handles_addon(self, handler, context):
        assert (
            handler.can_handle_line_item(_make_line_item(LineItemType.ADD_ON), context)
            is True
        )

    def test_rejects_token_bundle(self, handler, context):
        assert (
            handler.can_handle_line_item(
                _make_line_item(LineItemType.TOKEN_BUNDLE), context
            )
            is False
        )

    def test_rejects_custom(self, handler, context):
        assert (
            handler.can_handle_line_item(_make_line_item(LineItemType.CUSTOM), context)
            is False
        )


class TestActivateSubscription:
    def test_activates_pending_subscription(self, handler, context, container):
        subscription = MagicMock()
        subscription.status = SubscriptionStatus.PENDING
        subscription.tarif_plan = MagicMock()
        subscription.tarif_plan.billing_period = "MONTHLY"
        subscription.tarif_plan.features = {}
        subscription.tarif_plan.categories = []

        container.subscription_repository.return_value.find_by_id.return_value = (
            subscription
        )

        result = handler.activate_line_item(
            _make_line_item(LineItemType.SUBSCRIPTION), context
        )

        assert result.success is True
        assert subscription.status == SubscriptionStatus.ACTIVE


class TestCreditPlanDefaultTokens:
    """The plan's default_tokens must be credited THROUGH the TokenService seam
    (S138.0), never by mutating a balance row or building a TokenTransaction here.
    """

    def _subscription_with_default_tokens(self, default_tokens):
        subscription = MagicMock()
        subscription.id = uuid4()
        subscription.status = SubscriptionStatus.PENDING
        subscription.tarif_plan = MagicMock()
        subscription.tarif_plan.name = "Gold"
        subscription.tarif_plan.billing_period = "MONTHLY"
        subscription.tarif_plan.features = {"default_tokens": default_tokens}
        subscription.tarif_plan.categories = []
        return subscription

    def test_credits_default_tokens_through_token_service(
        self, handler, context, container
    ):
        subscription = self._subscription_with_default_tokens(50)
        container.subscription_repository.return_value.find_by_id.return_value = (
            subscription
        )

        result = handler.activate_line_item(
            _make_line_item(LineItemType.SUBSCRIPTION), context
        )

        assert result.success is True
        assert result.data["tokens_credited"] == 50

        token_service = container.token_service.return_value
        token_service.credit_tokens.assert_called_once_with(
            user_id=context.user_id,
            amount=50,
            transaction_type=TokenTransactionType.SUBSCRIPTION,
            reference_id=subscription.id,
            description="Plan tokens: Gold",
        )

    def test_does_not_touch_token_repositories_directly(
        self, handler, context, container
    ):
        subscription = self._subscription_with_default_tokens(50)
        container.subscription_repository.return_value.find_by_id.return_value = (
            subscription
        )

        handler.activate_line_item(_make_line_item(LineItemType.SUBSCRIPTION), context)

        container.token_balance_repository.return_value.save.assert_not_called()
        container.token_transaction_repository.return_value.save.assert_not_called()

    def test_no_tokens_credited_when_default_tokens_zero(
        self, handler, context, container
    ):
        subscription = self._subscription_with_default_tokens(0)
        container.subscription_repository.return_value.find_by_id.return_value = (
            subscription
        )

        result = handler.activate_line_item(
            _make_line_item(LineItemType.SUBSCRIPTION), context
        )

        assert result.data["tokens_credited"] == 0
        container.token_service.return_value.credit_tokens.assert_not_called()


class TestActivateAddon:
    def test_activates_pending_addon(self, handler, context, container):
        addon_subscription = MagicMock()
        addon_subscription.id = uuid4()
        addon_subscription.status = SubscriptionStatus.PENDING

        container.addon_subscription_repository.return_value.find_by_id.return_value = (
            addon_subscription
        )

        result = handler.activate_line_item(
            _make_line_item(LineItemType.ADD_ON), context
        )

        assert result.success is True
        assert addon_subscription.status == SubscriptionStatus.ACTIVE


class TestReverseSubscription:
    def test_cancels_active_subscription(self, handler, context, container):
        subscription = MagicMock()
        subscription.id = uuid4()
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.tarif_plan = MagicMock()
        subscription.tarif_plan.features = {}

        container.subscription_repository.return_value.find_by_id.return_value = (
            subscription
        )

        result = handler.reverse_line_item(
            _make_line_item(LineItemType.SUBSCRIPTION), context
        )

        assert result.success is True
        assert subscription.status == SubscriptionStatus.CANCELLED


class TestReverseAddon:
    def test_cancels_active_addon(self, handler, context, container):
        addon_subscription = MagicMock()
        addon_subscription.id = uuid4()
        addon_subscription.status = SubscriptionStatus.ACTIVE

        container.addon_subscription_repository.return_value.find_by_id.return_value = (
            addon_subscription
        )

        result = handler.reverse_line_item(
            _make_line_item(LineItemType.ADD_ON), context
        )

        assert result.success is True
        assert addon_subscription.status == SubscriptionStatus.CANCELLED
