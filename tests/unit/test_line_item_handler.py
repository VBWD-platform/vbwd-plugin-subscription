"""Unit tests for SubscriptionLineItemHandler (Sprint 04b)."""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from vbwd.events.line_item_registry import LineItemContext, LineItemResult
from plugins.subscription.subscription.handlers.line_item_handler import (
    SubscriptionLineItemHandler,
)
from vbwd.models.enums import LineItemType, SubscriptionStatus


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
