"""Tests for SubscriptionEntitlementProvider.

Relocated from core `tests/unit/services/test_feature_guard.py` (Sprint
03/S3). Same behaviour coverage (E2): plan features, free-tier fallback,
usage limits — now on the plugin's port implementation.
"""
import pytest
from unittest.mock import Mock
from uuid import uuid4
from datetime import datetime

from plugins.subscription.subscription.services.subscription_entitlement_provider import (  # noqa: E501
    SubscriptionEntitlementProvider,
)


class TestSubscriptionEntitlementProvider:
    @pytest.fixture
    def mock_subscription_repo(self):
        return Mock()

    @pytest.fixture
    def mock_usage_repo(self):
        return Mock()

    @pytest.fixture
    def provider(self, mock_subscription_repo, mock_usage_repo):
        return SubscriptionEntitlementProvider(mock_subscription_repo, mock_usage_repo)

    @pytest.fixture
    def mock_subscription(self):
        sub = Mock()
        sub.is_expired = False
        sub.tarif_plan = Mock()
        sub.tarif_plan.features = ["premium_feature", "api_access"]
        sub.current_period_start = datetime(2024, 1, 1)
        sub.start_date = datetime(2024, 1, 1)
        return sub

    def test_allowed_with_active_subscription(
        self, provider, mock_subscription_repo, mock_subscription
    ):
        mock_subscription_repo.find_active_by_user.return_value = mock_subscription
        assert provider.is_feature_allowed(uuid4(), "premium_feature") is True

    def test_denied_feature_not_in_plan(
        self, provider, mock_subscription_repo, mock_subscription
    ):
        mock_subscription_repo.find_active_by_user.return_value = mock_subscription
        assert provider.is_feature_allowed(uuid4(), "enterprise_only") is False

    def test_expired_subscription_uses_free_tier(
        self, provider, mock_subscription_repo, mock_subscription
    ):
        mock_subscription.is_expired = True
        mock_subscription_repo.find_active_by_user.return_value = mock_subscription
        assert provider.is_feature_allowed(uuid4(), "basic_access") is True
        assert provider.is_feature_allowed(uuid4(), "premium_feature") is False

    def test_no_subscription_uses_free_tier(self, provider, mock_subscription_repo):
        mock_subscription_repo.find_active_by_user.return_value = None
        assert provider.is_feature_allowed(uuid4(), "basic_access") is True
        assert provider.is_feature_allowed(uuid4(), "premium_feature") is False

    def test_usage_limit_enforced(
        self, provider, mock_subscription_repo, mock_usage_repo, mock_subscription
    ):
        mock_subscription.tarif_plan.features = {"limits": {"api_calls": 100}}
        mock_subscription_repo.find_active_by_user.return_value = mock_subscription
        mock_usage_repo.get_monthly_usage.return_value = 99
        allowed, remaining = provider.check_usage_limit(uuid4(), "api_calls", 1)
        assert allowed is True
        assert remaining == 0

    def test_usage_limit_exceeded(
        self, provider, mock_subscription_repo, mock_usage_repo, mock_subscription
    ):
        mock_subscription.tarif_plan.features = {"limits": {"api_calls": 100}}
        mock_subscription_repo.find_active_by_user.return_value = mock_subscription
        mock_usage_repo.get_monthly_usage.return_value = 100
        allowed, remaining = provider.check_usage_limit(uuid4(), "api_calls", 1)
        assert allowed is False
        assert remaining == 0

    def test_unlimited_feature_returns_none_remaining(
        self, provider, mock_subscription_repo, mock_usage_repo, mock_subscription
    ):
        mock_subscription.tarif_plan.features = []
        mock_subscription_repo.find_active_by_user.return_value = mock_subscription
        allowed, remaining = provider.check_usage_limit(uuid4(), "unlimited_feature")
        assert allowed is True
        assert remaining is None

    def test_get_feature_limits_returns_usage_stats(
        self, provider, mock_subscription_repo, mock_usage_repo, mock_subscription
    ):
        mock_subscription.tarif_plan.features = {
            "limits": {"api_calls": 100, "exports": 10}
        }
        mock_subscription_repo.find_active_by_user.return_value = mock_subscription
        mock_usage_repo.get_monthly_usage.side_effect = [50, 3]
        result = provider.get_feature_limits(uuid4())
        assert result["api_calls"] == {"limit": 100, "used": 50, "remaining": 50}
        assert result["exports"] == {"limit": 10, "used": 3, "remaining": 7}

    def test_get_feature_limits_empty_without_subscription(
        self, provider, mock_subscription_repo
    ):
        mock_subscription_repo.find_active_by_user.return_value = None
        assert provider.get_feature_limits(uuid4()) == {}

    def test_get_user_features_combines_plan_and_free_tier(
        self, provider, mock_subscription_repo, mock_subscription
    ):
        mock_subscription_repo.find_active_by_user.return_value = mock_subscription
        result = provider.get_user_features(uuid4())
        assert "premium_feature" in result
        assert "api_access" in result
        assert "basic_access" in result

    def test_check_usage_limit_no_subscription(self, provider, mock_subscription_repo):
        mock_subscription_repo.find_active_by_user.return_value = None
        allowed, remaining = provider.check_usage_limit(uuid4(), "api_calls")
        assert allowed is False
        assert remaining is None

    def test_check_usage_limit_increments_on_success(
        self, provider, mock_subscription_repo, mock_usage_repo, mock_subscription
    ):
        mock_subscription.tarif_plan.features = {"limits": {"api_calls": 100}}
        mock_subscription_repo.find_active_by_user.return_value = mock_subscription
        mock_usage_repo.get_monthly_usage.return_value = 50
        provider.check_usage_limit(uuid4(), "api_calls", 5)
        mock_usage_repo.increment_usage.assert_called_once()
