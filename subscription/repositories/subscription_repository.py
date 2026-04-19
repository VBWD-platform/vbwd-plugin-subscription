"""Subscription repository implementation."""
from datetime import timedelta
from typing import Optional, List, Union, Tuple
from uuid import UUID
from vbwd.utils.datetime_utils import utcnow
from vbwd.repositories.base import BaseRepository
from plugins.subscription.subscription.models import Subscription
from vbwd.models.enums import SubscriptionStatus


class SubscriptionRepository(BaseRepository[Subscription]):
    """Repository for Subscription entity operations."""

    def __init__(self, session):
        super().__init__(session=session, model=Subscription)

    def find_by_user(self, user_id: Union[UUID, str]) -> List[Subscription]:
        """Find all subscriptions for a user."""
        return (
            self._session.query(Subscription)
            .filter(Subscription.user_id == user_id)
            .order_by(Subscription.created_at.desc())
            .all()
        )

    def find_active_by_user(self, user_id: Union[UUID, str]) -> Optional[Subscription]:
        """Find active or trialing subscription for a user."""
        return (
            self._session.query(Subscription)
            .filter(
                Subscription.user_id == user_id,
                Subscription.status.in_(
                    [
                        SubscriptionStatus.ACTIVE,
                        SubscriptionStatus.TRIALING,
                    ]
                ),
            )
            .first()
        )

    def find_by_provider_subscription_id(
        self, provider_subscription_id: str
    ) -> Optional[Subscription]:
        """Find subscription by external provider subscription ID."""
        return (
            self._session.query(Subscription)
            .filter(Subscription.provider_subscription_id == provider_subscription_id)
            .first()
        )

    def find_expiring_soon(self, days: int = 7) -> List[Subscription]:
        """Find subscriptions expiring within specified days."""

        threshold = utcnow() + timedelta(days=days)
        return (
            self._session.query(Subscription)
            .filter(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at <= threshold,
            )
            .all()
        )

    def find_expired(self) -> List[Subscription]:
        """Find subscriptions that have expired."""
        return (
            self._session.query(Subscription)
            .filter(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at < utcnow(),
            )
            .all()
        )

    def find_expired_trials(self) -> List[Subscription]:
        """Find trialing subscriptions whose trial has ended."""
        return (
            self._session.query(Subscription)
            .filter(
                Subscription.status == SubscriptionStatus.TRIALING,
                Subscription.trial_end_at <= utcnow(),
            )
            .all()
        )

    def find_active_by_user_and_plan(
        self, user_id: Union[UUID, str], plan_id: Union[UUID, str]
    ) -> Optional[Subscription]:
        """Find active subscription for a user with a specific plan."""
        return (
            self._session.query(Subscription)
            .filter(
                Subscription.user_id == user_id,
                Subscription.tarif_plan_id == plan_id,
                Subscription.status.in_(
                    [
                        SubscriptionStatus.ACTIVE,
                        SubscriptionStatus.TRIALING,
                    ]
                ),
            )
            .first()
        )

    def find_active_by_user_in_category(
        self, user_id: Union[UUID, str], category_plan_ids: list
    ) -> List[Subscription]:
        """Find all active subscriptions for a user within a set of plan IDs (category)."""
        if not category_plan_ids:
            return []
        return (
            self._session.query(Subscription)
            .filter(
                Subscription.user_id == user_id,
                Subscription.tarif_plan_id.in_(category_plan_ids),
                Subscription.status.in_(
                    [
                        SubscriptionStatus.ACTIVE,
                        SubscriptionStatus.TRIALING,
                    ]
                ),
            )
            .all()
        )

    def find_all_active_by_user(self, user_id: Union[UUID, str]) -> List[Subscription]:
        """Find all active/trialing subscriptions for a user (across all categories)."""
        return (
            self._session.query(Subscription)
            .filter(
                Subscription.user_id == user_id,
                Subscription.status.in_(
                    [
                        SubscriptionStatus.ACTIVE,
                        SubscriptionStatus.TRIALING,
                    ]
                ),
            )
            .order_by(Subscription.created_at.desc())
            .all()
        )

    def find_dunning_candidates(self, days_since_failure: int) -> List[Subscription]:
        """Find active subscriptions whose payment_failed_at was exactly N days ago."""
        target_start = utcnow() - timedelta(days=days_since_failure + 1)
        target_end = utcnow() - timedelta(days=days_since_failure)
        return (
            self._session.query(Subscription)
            .filter(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.payment_failed_at.isnot(None),
                Subscription.payment_failed_at >= target_start,
                Subscription.payment_failed_at < target_end,
            )
            .all()
        )

    def find_all_paginated(
        self,
        limit: int = 20,
        offset: int = 0,
        status: Optional[str] = None,
        user_id: Optional[str] = None,
        plan_id: Optional[str] = None,
    ) -> Tuple[List[Subscription], int]:
        """
        Find all subscriptions with pagination and filters.

        Args:
            limit: Maximum number of results.
            offset: Number of results to skip.
            status: Optional status filter.
            user_id: Optional user_id filter.
            plan_id: Optional plan_id filter.

        Returns:
            Tuple of (subscriptions list, total count).
        """
        query = self._session.query(Subscription)

        # Apply status filter
        if status:
            try:
                status_enum = SubscriptionStatus(status)
                query = query.filter(Subscription.status == status_enum)
            except ValueError:
                pass

        # Apply user filter
        if user_id:
            query = query.filter(Subscription.user_id == user_id)

        # Apply plan filter
        if plan_id:
            query = query.filter(Subscription.tarif_plan_id == plan_id)

        # Get total count before pagination
        total = query.count()

        # Apply pagination
        subscriptions = (
            query.order_by(Subscription.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        return subscriptions, total
