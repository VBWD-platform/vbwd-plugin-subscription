"""Subscription service implementation."""
from datetime import timedelta
from vbwd.utils.datetime_utils import utcnow
from decimal import Decimal
from typing import Optional, List
from uuid import UUID
from plugins.subscription.subscription.repositories.subscription_repository import SubscriptionRepository
from plugins.subscription.subscription.repositories.tarif_plan_repository import TarifPlanRepository
from plugins.subscription.subscription.models import Subscription
from vbwd.models.invoice import UserInvoice
from vbwd.models.enums import (
    SubscriptionStatus,
    BillingPeriod,
    InvoiceStatus,
    TokenTransactionType,
)


class SubscriptionResult:
    """Result of a subscription operation."""

    def __init__(
        self,
        success: bool,
        subscription: Optional[Subscription] = None,
        error: Optional[str] = None,
    ):
        """
        Initialize subscription result.

        Args:
            success: Whether the operation succeeded.
            subscription: The subscription if successful.
            error: Error message if failed.
        """
        self.success = success
        self.subscription = subscription
        self.error = error


class ProrationResult:
    """Result of a proration calculation."""

    def __init__(self, credit: Decimal, amount_due: Decimal, days_remaining: int):
        """
        Initialize proration result.

        Args:
            credit: Credit for unused time.
            amount_due: Amount due for new plan.
            days_remaining: Days remaining on current plan.
        """
        self.credit = credit
        self.amount_due = amount_due
        self.days_remaining = days_remaining


class SubscriptionService:
    """
    Subscription lifecycle management service.

    Handles subscription creation, activation, cancellation, and retrieval.
    """

    # Duration in days for each billing period
    PERIOD_DAYS = {
        BillingPeriod.DAILY: 1,
        BillingPeriod.WEEKLY: 7,
        BillingPeriod.MONTHLY: 30,
        BillingPeriod.QUARTERLY: 90,
        BillingPeriod.YEARLY: 365,
        BillingPeriod.ONE_TIME: 36500,  # ~100 years for lifetime
    }

    DUNNING_DAYS = [3, 7]

    def __init__(
        self,
        subscription_repo: SubscriptionRepository,
        tarif_plan_repo: Optional[TarifPlanRepository] = None,
        token_service=None,
    ):
        """Initialize SubscriptionService.

        Args:
            subscription_repo: Repository for subscription data access
            tarif_plan_repo: Optional repository for tariff plan validation
            token_service: Optional TokenService for crediting plan tokens
        """
        self._subscription_repo = subscription_repo
        self._tarif_plan_repo = tarif_plan_repo
        self._token_service = token_service

    def get_active_subscriptions(self, user_id: UUID) -> List[Subscription]:
        """Get all active/trialing subscriptions for a user (all categories).

        Args:
            user_id: User UUID

        Returns:
            List of active subscriptions
        """
        return self._subscription_repo.find_all_active_by_user(user_id)

    def get_active_subscription(self, user_id: UUID) -> Optional[Subscription]:
        """Get user's active subscription.

        Args:
            user_id: User UUID

        Returns:
            Active subscription if found, None otherwise
        """
        return self._subscription_repo.find_active_by_user(user_id)

    def get_user_subscriptions(self, user_id: UUID) -> List[Subscription]:
        """Get all user subscriptions.

        Args:
            user_id: User UUID

        Returns:
            List of all user subscriptions
        """
        return self._subscription_repo.find_by_user(user_id)

    def create_subscription(
        self,
        user_id: UUID,
        tarif_plan_id: UUID,
    ) -> Subscription:
        """
        Create new subscription (pending until payment).

        Args:
            user_id: User UUID
            tarif_plan_id: Tariff plan UUID

        Returns:
            Created subscription with pending status

        Raises:
            ValueError: If plan not found or not active
        """
        # Verify plan exists and is active
        if self._tarif_plan_repo:
            plan = self._tarif_plan_repo.find_by_id(tarif_plan_id)
            if not plan:
                raise ValueError(f"Tariff plan {tarif_plan_id} not found")
            if not plan.is_active:
                raise ValueError(f"Tariff plan {tarif_plan_id} is not active")

        subscription = Subscription()
        subscription.user_id = user_id
        subscription.tarif_plan_id = tarif_plan_id
        subscription.status = SubscriptionStatus.PENDING

        return self._subscription_repo.save(subscription)

    def start_trial(
        self, user_id: UUID, tarif_plan_id: UUID, user_repo
    ) -> SubscriptionResult:
        """
        Start a trial subscription for a user.

        Args:
            user_id: User UUID
            tarif_plan_id: Tariff plan UUID
            user_repo: UserRepository (passed, not owned by this service)

        Returns:
            SubscriptionResult with trialing subscription
        """
        user = user_repo.find_by_id(user_id)
        if not user:
            return SubscriptionResult(success=False, error="User not found")

        if user.has_used_trial:
            return SubscriptionResult(
                success=False, error="User has already used a trial"
            )

        existing = self._subscription_repo.find_active_by_user(user_id)
        if existing:
            return SubscriptionResult(
                success=False, error="User already has an active subscription"
            )

        if not self._tarif_plan_repo:
            return SubscriptionResult(
                success=False, error="Plan repository not available"
            )

        plan = self._tarif_plan_repo.find_by_id(tarif_plan_id)
        if not plan:
            return SubscriptionResult(success=False, error="Plan not found")
        if plan.trial_days <= 0:
            return SubscriptionResult(success=False, error="Plan has no trial period")

        subscription = Subscription()
        subscription.user_id = user_id
        subscription.tarif_plan_id = tarif_plan_id
        subscription.start_trial(plan.trial_days)

        saved = self._subscription_repo.save(subscription)

        user.has_used_trial = True
        user_repo.save(user)

        return SubscriptionResult(success=True, subscription=saved)

    def activate_subscription(self, subscription_id: UUID) -> SubscriptionResult:
        """
        Activate subscription after payment.

        Sets status to active and calculates expiration date
        based on tariff plan billing period.

        Args:
            subscription_id: Subscription UUID

        Returns:
            SubscriptionResult with activated subscription
        """
        subscription = self._subscription_repo.find_by_id(subscription_id)
        if not subscription:
            return SubscriptionResult(success=False, error="Subscription not found")

        if subscription.status == SubscriptionStatus.TRIALING:
            return SubscriptionResult(
                success=False,
                error="Trial subscriptions can only be activated via invoice payment",
            )

        # Get plan for duration
        plan = subscription.tarif_plan
        duration_days = self.PERIOD_DAYS.get(plan.billing_period, 30)

        # Activate using model method
        subscription.activate(duration_days)

        # Credit default tokens from plan features
        features = plan.features or {}
        default_tokens = (
            features.get("default_tokens", 0) if isinstance(features, dict) else 0
        )
        if self._token_service and default_tokens > 0:
            self._token_service.credit_tokens(
                user_id=subscription.user_id,
                amount=default_tokens,
                transaction_type=TokenTransactionType.SUBSCRIPTION,
                reference_id=subscription.id,
                description=f"Plan tokens: {plan.name}",
            )

        saved = self._subscription_repo.save(subscription)

        return SubscriptionResult(success=True, subscription=saved)

    def cancel_subscription(self, subscription_id: UUID) -> SubscriptionResult:
        """
        Cancel subscription.

        Args:
            subscription_id: Subscription UUID

        Returns:
            SubscriptionResult with cancelled subscription
        """
        subscription = self._subscription_repo.find_by_id(subscription_id)
        if not subscription:
            return SubscriptionResult(success=False, error="Subscription not found")

        # Cancel using model method
        subscription.cancel()
        saved = self._subscription_repo.save(subscription)

        return SubscriptionResult(success=True, subscription=saved)

    def renew_subscription(self, subscription_id: UUID) -> Subscription:
        """
        Renew an expired subscription.

        Args:
            subscription_id: Subscription UUID

        Returns:
            Renewed subscription

        Raises:
            ValueError: If subscription not found
        """
        subscription = self._subscription_repo.find_by_id(subscription_id)
        if not subscription:
            raise ValueError(f"Subscription {subscription_id} not found")

        # Get plan for duration
        plan = subscription.tarif_plan
        duration_days = self.PERIOD_DAYS.get(plan.billing_period, 30)

        # Reactivate
        subscription.activate(duration_days)

        return self._subscription_repo.save(subscription)

    def get_expiring_subscriptions(self, days: int = 7) -> List[Subscription]:
        """
        Get subscriptions expiring within specified days.

        Useful for sending renewal reminders.

        Args:
            days: Number of days threshold

        Returns:
            List of expiring subscriptions
        """
        return self._subscription_repo.find_expiring_soon(days)

    def expire_subscriptions(self) -> List[Subscription]:
        """
        Find and mark expired subscriptions.

        Returns:
            List of expired subscriptions
        """
        expired = self._subscription_repo.find_expired()

        for subscription in expired:
            subscription.expire()
            self._subscription_repo.save(subscription)

        return expired

    def expire_trials(self, invoice_repo) -> list:
        """
        Find and cancel expired trial subscriptions, creating pending invoices.

        Args:
            invoice_repo: InvoiceRepository for creating invoices

        Returns:
            List of dicts with subscription_id and invoice_id
        """
        expired_trials = self._subscription_repo.find_expired_trials()
        results = []

        for subscription in expired_trials:
            subscription.cancel()
            self._subscription_repo.save(subscription)

            plan = subscription.tarif_plan
            invoice = UserInvoice()
            invoice.user_id = subscription.user_id
            invoice.tarif_plan_id = plan.id
            invoice.subscription_id = subscription.id
            invoice.invoice_number = UserInvoice.generate_invoice_number()
            invoice.amount = plan.price or plan.price_float or 0
            invoice.currency = plan.currency or "EUR"
            invoice.status = InvoiceStatus.PENDING
            invoice.invoiced_at = utcnow()
            invoice.expires_at = utcnow() + timedelta(days=30)
            invoice_repo.save(invoice)

            results.append(
                {
                    "subscription_id": str(subscription.id),
                    "invoice_id": str(invoice.id),
                }
            )

        return results

    def send_dunning_emails(self, event_dispatcher=None) -> list:
        """
        Send dunning emails for subscriptions with failed payments.

        Args:
            event_dispatcher: Optional event dispatcher to emit dunning events

        Returns:
            List of dicts with subscription_id and days_overdue
        """
        from vbwd.events.subscription_events import SubscriptionDunningEvent

        results = []
        for days in self.DUNNING_DAYS:
            candidates = self._subscription_repo.find_dunning_candidates(days)
            for subscription in candidates:
                if event_dispatcher is not None:
                    event = SubscriptionDunningEvent(
                        subscription_id=subscription.id,  # type: ignore[arg-type]
                        user_id=subscription.user_id,  # type: ignore[arg-type]
                        days_overdue=days,
                    )
                    event_dispatcher.emit(event)
                results.append(
                    {
                        "subscription_id": str(subscription.id),
                        "days_overdue": days,
                    }
                )
        return results

    def pause_subscription(self, subscription_id: UUID) -> SubscriptionResult:
        """
        Pause an active subscription.

        The subscription will stop being billed and the expiration
        will be extended when resumed.

        Args:
            subscription_id: Subscription UUID

        Returns:
            SubscriptionResult with paused subscription
        """
        subscription = self._subscription_repo.find_by_id(subscription_id)
        if not subscription:
            return SubscriptionResult(success=False, error="Subscription not found")

        if subscription.status == SubscriptionStatus.PAUSED:
            return SubscriptionResult(
                success=False, error="Subscription is already paused"
            )

        if subscription.status != SubscriptionStatus.ACTIVE:
            return SubscriptionResult(
                success=False, error="Only active subscriptions can be paused"
            )

        subscription.pause()
        saved = self._subscription_repo.save(subscription)

        return SubscriptionResult(success=True, subscription=saved)

    def resume_subscription(self, subscription_id: UUID) -> SubscriptionResult:
        """
        Resume a paused subscription.

        The expiration date is extended by the duration of the pause.

        Args:
            subscription_id: Subscription UUID

        Returns:
            SubscriptionResult with resumed subscription
        """
        subscription = self._subscription_repo.find_by_id(subscription_id)
        if not subscription:
            return SubscriptionResult(success=False, error="Subscription not found")

        if subscription.status != SubscriptionStatus.PAUSED:
            return SubscriptionResult(success=False, error="Subscription is not paused")

        # Calculate pause duration and extend expiration
        if subscription.paused_at and subscription.expires_at:
            paused_duration = utcnow() - subscription.paused_at
            subscription.expires_at = subscription.expires_at + paused_duration

        subscription.resume()
        saved = self._subscription_repo.save(subscription)

        return SubscriptionResult(success=True, subscription=saved)

    def calculate_proration(
        self, subscription_id: UUID, new_plan_id: UUID
    ) -> Optional[ProrationResult]:
        """
        Calculate proration for plan change.

        Returns credit for unused time and amount due for new plan.

        Args:
            subscription_id: Subscription UUID
            new_plan_id: New plan UUID

        Returns:
            ProrationResult with credit and amount due, or None if calculation fails
        """
        if not self._tarif_plan_repo:
            return None

        subscription = self._subscription_repo.find_by_id(subscription_id)
        if not subscription:
            return None

        current_plan = self._tarif_plan_repo.find_by_id(subscription.tarif_plan_id)
        new_plan = self._tarif_plan_repo.find_by_id(new_plan_id)

        if not current_plan or not new_plan:
            return None

        # Calculate days remaining
        if not subscription.expires_at:
            return None

        days_remaining = max(0, (subscription.expires_at - utcnow()).days)
        total_days = self.PERIOD_DAYS.get(current_plan.billing_period, 30)

        # Credit for unused time
        daily_rate = current_plan.price / Decimal(total_days)
        credit = daily_rate * Decimal(days_remaining)

        # Amount due for new plan
        amount_due = new_plan.price - credit

        return ProrationResult(
            credit=credit.quantize(Decimal("0.01")),
            amount_due=max(amount_due, Decimal("0")).quantize(Decimal("0.01")),
            days_remaining=days_remaining,
        )

    def upgrade_subscription(
        self, subscription_id: UUID, new_plan_id: UUID
    ) -> SubscriptionResult:
        """
        Upgrade subscription to higher tier plan immediately.

        Args:
            subscription_id: Subscription UUID
            new_plan_id: New plan UUID

        Returns:
            SubscriptionResult with upgraded subscription
        """
        subscription = self._subscription_repo.find_by_id(subscription_id)
        if not subscription:
            return SubscriptionResult(success=False, error="Subscription not found")

        if str(subscription.tarif_plan_id) == str(new_plan_id):
            return SubscriptionResult(
                success=False, error="Already subscribed to this plan"
            )

        if subscription.status != SubscriptionStatus.ACTIVE:
            return SubscriptionResult(
                success=False, error="Only active subscriptions can be upgraded"
            )

        # Change plan immediately
        subscription.tarif_plan_id = new_plan_id
        subscription.pending_plan_id = None
        saved = self._subscription_repo.save(subscription)

        return SubscriptionResult(success=True, subscription=saved)

    def downgrade_subscription(
        self, subscription_id: UUID, new_plan_id: UUID
    ) -> SubscriptionResult:
        """
        Downgrade subscription at next renewal.

        Args:
            subscription_id: Subscription UUID
            new_plan_id: New plan UUID

        Returns:
            SubscriptionResult with subscription having pending_plan_id set
        """
        subscription = self._subscription_repo.find_by_id(subscription_id)
        if not subscription:
            return SubscriptionResult(success=False, error="Subscription not found")

        if str(subscription.tarif_plan_id) == str(new_plan_id):
            return SubscriptionResult(
                success=False, error="Already subscribed to this plan"
            )

        if subscription.status != SubscriptionStatus.ACTIVE:
            return SubscriptionResult(
                success=False, error="Only active subscriptions can be downgraded"
            )

        # Set pending plan change (takes effect at renewal)
        subscription.pending_plan_id = new_plan_id
        saved = self._subscription_repo.save(subscription)

        return SubscriptionResult(success=True, subscription=saved)
