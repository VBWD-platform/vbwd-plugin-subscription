"""Subscription entitlement provider.

The feature-gating logic relocated from core `FeatureGuard` (Sprint 03/S3),
now implementing the generic core `IEntitlementProvider` port. Behaviour is
unchanged (E2): plan features + free-tier fallback + usage limits.
"""
from typing import Any, Optional, Dict, Tuple, Set
from uuid import UUID

from vbwd.services.entitlement import IEntitlementProvider


class SubscriptionEntitlementProvider(IEntitlementProvider):
    """Tariff-plan based feature access + usage limits."""

    FREE_TIER_FEATURES: Set[str] = {
        "basic_access",
        "limited_uploads",
        "standard_support",
    }

    def __init__(self, subscription_repo=None, usage_repo=None):
        # Explicit repos for tests; None ⇒ build lazily per call from the
        # request-scoped session (matches the plugin's route pattern).
        self._subscription_repo = subscription_repo
        self._usage_repo = usage_repo

    @property
    def subscription_repo(self):
        if self._subscription_repo is not None:
            return self._subscription_repo
        from vbwd.extensions import db
        from plugins.subscription.subscription.repositories.subscription_repository import (  # noqa: E501
            SubscriptionRepository,
        )

        return SubscriptionRepository(db.session)

    @property
    def usage_repo(self):
        if self._usage_repo is not None:
            return self._usage_repo
        from vbwd.extensions import db
        from vbwd.repositories.feature_usage_repository import (
            FeatureUsageRepository,
        )

        return FeatureUsageRepository(db.session)

    def is_feature_allowed(self, user_id: UUID, feature_name: str) -> bool:
        subscription = self.subscription_repo.find_active_by_user(user_id)

        if not subscription:
            return feature_name in self.FREE_TIER_FEATURES

        if subscription.is_expired:
            return feature_name in self.FREE_TIER_FEATURES

        plan_features = subscription.tarif_plan.features or []
        return feature_name in plan_features

    def check_usage_limit(
        self, user_id: UUID, feature_name: str, amount: int = 1
    ) -> Tuple[bool, Optional[int]]:
        subscription = self.subscription_repo.find_active_by_user(user_id)
        if not subscription:
            return False, None

        limit = self._get_feature_limit(subscription.tarif_plan, feature_name)
        if limit is None:
            return True, None

        period_start = subscription.current_period_start or subscription.start_date
        current_usage = self.usage_repo.get_monthly_usage(
            user_id, feature_name, period_start
        )

        remaining = limit - current_usage
        if remaining >= amount:
            self.usage_repo.increment_usage(user_id, feature_name, period_start, amount)
            return True, remaining - amount

        return False, remaining

    def get_feature_value(
        self, user_id: UUID, feature_name: str, default: Any = None
    ) -> Any:
        # Read a plan-feature value for the user's active (ACTIVE/TRIALING) plan.
        # Mirrors taro's prior direct read of tarif_plan.features[name].
        subscription = self.subscription_repo.find_active_by_user(user_id)
        if not subscription or not subscription.tarif_plan:
            return default
        features = subscription.tarif_plan.features
        if isinstance(features, dict):
            return features.get(feature_name, default)
        return default

    def current_plan_name(self, user_id: UUID) -> Optional[str]:
        subscription = self.subscription_repo.find_active_by_user(user_id)
        if subscription and subscription.tarif_plan:
            return subscription.tarif_plan.name
        return None

    def get_feature_limits(self, user_id: UUID) -> Dict[str, dict]:
        subscription = self.subscription_repo.find_active_by_user(user_id)
        if not subscription:
            return {}

        limits = self._get_plan_limits(subscription.tarif_plan)
        if not limits:
            return {}

        period_start = subscription.current_period_start or subscription.start_date
        result = {}
        for feature_name, limit in limits.items():
            usage = self.usage_repo.get_monthly_usage(
                user_id, feature_name, period_start
            )
            result[feature_name] = {
                "limit": limit,
                "used": usage,
                "remaining": max(0, limit - usage),
            }
        return result

    def get_user_features(self, user_id: UUID) -> Set[str]:
        subscription = self.subscription_repo.find_active_by_user(user_id)
        if not subscription or subscription.is_expired:
            return self.FREE_TIER_FEATURES.copy()
        plan_features = set(subscription.tarif_plan.features or [])
        return plan_features | self.FREE_TIER_FEATURES

    def _get_feature_limit(self, tarif_plan, feature_name: str) -> Optional[int]:
        limits = self._get_plan_limits(tarif_plan)
        return limits.get(feature_name)

    def _get_plan_limits(self, tarif_plan) -> Dict[str, int]:
        features = tarif_plan.features or []
        if isinstance(features, dict):
            return features.get("limits", {})
        return {}
