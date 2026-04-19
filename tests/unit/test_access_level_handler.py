"""Unit tests for SubscriptionAccessLevelHandler (Sprint 17b)."""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from plugins.subscription.subscription.handlers.access_level_handler import (
    SubscriptionAccessLevelHandler,
    FALLBACK_LEVEL_SLUG,
)


@pytest.fixture()
def handler():
    return SubscriptionAccessLevelHandler()


@pytest.fixture()
def mock_service():
    with patch(
        "vbwd.services.user_access_level_service.UserAccessLevelService"
    ) as mock_cls:
        service_instance = MagicMock()
        mock_cls.return_value = service_instance
        yield service_instance


class TestOnSubscriptionActivated:
    def test_assigns_plan_linked_level(self, handler, mock_service):
        """Should assign the access level linked to the plan slug."""
        user_id = uuid4()
        level = MagicMock()
        level.id = uuid4()
        level.slug = "subscribed-basic"
        mock_service.find_by_linked_plan_slug.return_value = level

        handler.on_subscription_activated(
            "subscription.activated",
            {
                "user_id": str(user_id),
                "plan_slug": "basic",
                "plan_id": str(uuid4()),
                "subscription_id": str(uuid4()),
            },
        )

        mock_service.find_by_linked_plan_slug.assert_called_once_with("basic")
        mock_service.assign.assert_called_once_with(user_id, level.id)

    def test_skips_when_no_linked_level(self, handler, mock_service):
        """Should skip assignment when no access level is linked to the plan."""
        mock_service.find_by_linked_plan_slug.return_value = None

        handler.on_subscription_activated(
            "subscription.activated",
            {
                "user_id": str(uuid4()),
                "plan_slug": "unknown-plan",
            },
        )

        mock_service.assign.assert_not_called()

    def test_skips_when_missing_user_id(self, handler, mock_service):
        """Should skip when payload is missing user_id."""
        handler.on_subscription_activated(
            "subscription.activated",
            {"plan_slug": "basic"},
        )

        mock_service.find_by_linked_plan_slug.assert_not_called()

    def test_skips_when_missing_plan_slug(self, handler, mock_service):
        """Should skip when payload is missing plan_slug."""
        handler.on_subscription_activated(
            "subscription.activated",
            {"user_id": str(uuid4())},
        )

        mock_service.find_by_linked_plan_slug.assert_not_called()

    def test_handles_service_exception(self, handler, mock_service):
        """Should not raise when service throws."""
        mock_service.find_by_linked_plan_slug.side_effect = RuntimeError("DB error")

        handler.on_subscription_activated(
            "subscription.activated",
            {
                "user_id": str(uuid4()),
                "plan_slug": "basic",
            },
        )
        # Should not raise


class TestOnSubscriptionCancelled:
    def test_revokes_plan_linked_levels_and_assigns_fallback(
        self, handler, mock_service
    ):
        """Should revoke plan-linked levels and assign the fallback level."""
        user_id = uuid4()
        fallback_level = MagicMock()
        fallback_level.id = uuid4()
        fallback_level.slug = FALLBACK_LEVEL_SLUG

        mock_service.revoke_plan_linked_levels.return_value = 1
        mock_service.find_by_slug.return_value = fallback_level

        handler.on_subscription_cancelled(
            "subscription.cancelled",
            {
                "user_id": str(user_id),
                "plan_slug": "basic",
                "subscription_id": str(uuid4()),
            },
        )

        mock_service.revoke_plan_linked_levels.assert_called_once_with(
            user_id, "basic"
        )
        mock_service.find_by_slug.assert_called_once_with(FALLBACK_LEVEL_SLUG)
        mock_service.assign.assert_called_once_with(user_id, fallback_level.id)

    def test_no_fallback_when_nothing_revoked(self, handler, mock_service):
        """Should not assign fallback if no levels were revoked."""
        mock_service.revoke_plan_linked_levels.return_value = 0

        handler.on_subscription_cancelled(
            "subscription.cancelled",
            {
                "user_id": str(uuid4()),
                "plan_slug": "basic",
            },
        )

        mock_service.find_by_slug.assert_not_called()
        mock_service.assign.assert_not_called()

    def test_skips_when_missing_user_id(self, handler, mock_service):
        """Should skip when payload is missing user_id."""
        handler.on_subscription_cancelled(
            "subscription.cancelled",
            {"plan_slug": "basic"},
        )

        mock_service.revoke_plan_linked_levels.assert_not_called()

    def test_skips_when_missing_plan_slug(self, handler, mock_service):
        """Should skip when payload is missing plan_slug."""
        handler.on_subscription_cancelled(
            "subscription.cancelled",
            {"user_id": str(uuid4())},
        )

        mock_service.revoke_plan_linked_levels.assert_not_called()

    def test_warns_when_fallback_level_missing(self, handler, mock_service):
        """Should warn when fallback level doesn't exist in DB."""
        mock_service.revoke_plan_linked_levels.return_value = 1
        mock_service.find_by_slug.return_value = None

        handler.on_subscription_cancelled(
            "subscription.cancelled",
            {
                "user_id": str(uuid4()),
                "plan_slug": "basic",
            },
        )

        mock_service.assign.assert_not_called()

    def test_handles_service_exception(self, handler, mock_service):
        """Should not raise when service throws."""
        mock_service.revoke_plan_linked_levels.side_effect = RuntimeError(
            "DB error"
        )

        handler.on_subscription_cancelled(
            "subscription.cancelled",
            {
                "user_id": str(uuid4()),
                "plan_slug": "basic",
            },
        )
        # Should not raise
