"""SubscriptionLineItemHandler — handles SUBSCRIPTION + ADD_ON line items.

Registered by the subscription plugin via register_line_item_handlers().
Extracted from CoreLineItemHandler (Sprint 04b).
"""
import logging
from datetime import timedelta
from uuid import uuid4

from vbwd.events.line_item_registry import (
    ILineItemHandler,
    LineItemContext,
    LineItemResult,
)
from vbwd.models.enums import (
    LineItemType,
    SubscriptionStatus,
    TokenTransactionType,
)
from vbwd.utils.datetime_utils import utcnow

logger = logging.getLogger(__name__)


class SubscriptionLineItemHandler(ILineItemHandler):
    """Handles SUBSCRIPTION and ADD_ON line items."""

    HANDLED_TYPES = {LineItemType.SUBSCRIPTION, LineItemType.ADD_ON}

    def __init__(self, container):
        self._container = container

    def can_handle_line_item(self, line_item, context: LineItemContext) -> bool:
        return line_item.item_type in self.HANDLED_TYPES

    def activate_line_item(self, line_item, context: LineItemContext) -> LineItemResult:
        if line_item.item_type == LineItemType.SUBSCRIPTION:
            return self._activate_subscription(line_item, context)
        elif line_item.item_type == LineItemType.ADD_ON:
            return self._activate_addon(line_item, context)
        return LineItemResult.skip()

    def reverse_line_item(self, line_item, context: LineItemContext) -> LineItemResult:
        if line_item.item_type == LineItemType.SUBSCRIPTION:
            return self._reverse_subscription(line_item, context)
        elif line_item.item_type == LineItemType.ADD_ON:
            return self._reverse_addon(line_item, context)
        return LineItemResult.skip()

    def restore_line_item(self, line_item, context: LineItemContext) -> LineItemResult:
        if line_item.item_type == LineItemType.SUBSCRIPTION:
            return self._restore_subscription(line_item, context)
        elif line_item.item_type == LineItemType.ADD_ON:
            return self._restore_addon(line_item, context)
        return LineItemResult.skip()

    # ── Activation ────────────────────────────────────────────────────────

    def _activate_subscription(
        self, line_item, context: LineItemContext
    ) -> LineItemResult:
        subscription_repo = self._container.subscription_repository()
        subscription = subscription_repo.find_by_id(line_item.item_id)
        if not subscription or subscription.status not in (
            SubscriptionStatus.PENDING,
            SubscriptionStatus.TRIALING,
            SubscriptionStatus.CANCELLED,
        ):
            return LineItemResult(success=True, data={})

        # Cancel conflicting subscriptions in is_single categories
        plan = subscription.tarif_plan
        categories = getattr(plan, "categories", []) if plan else []
        cancelled_conflicting = []
        for category in categories:
            if category.is_single:
                category_plan_ids = [str(p.id) for p in category.tarif_plans]
                conflicting = subscription_repo.find_active_by_user_in_category(
                    context.user_id, category_plan_ids
                )
                for previous_subscription in conflicting:
                    if str(previous_subscription.id) != str(subscription.id):
                        previous_subscription.status = SubscriptionStatus.CANCELLED
                        previous_subscription.cancelled_at = utcnow()
                        subscription_repo.save(previous_subscription)
                        cancelled_conflicting.append(previous_subscription)

        subscription.status = SubscriptionStatus.ACTIVE
        subscription.started_at = utcnow()

        if subscription.tarif_plan:
            from plugins.subscription.subscription.services.subscription_service import (
                SubscriptionService,
            )

            period_days = SubscriptionService.PERIOD_DAYS.get(
                subscription.tarif_plan.billing_period, 30
            )
            subscription.expires_at = utcnow() + timedelta(days=period_days)

        subscription_repo.save(subscription)

        tokens_credited = self._credit_plan_default_tokens(subscription, context)

        # Publish subscription lifecycle events to EventBus
        self._publish_subscription_event(
            "subscription.activated", subscription, context.user_id
        )
        for cancelled_sub in cancelled_conflicting:
            self._publish_subscription_event(
                "subscription.cancelled", cancelled_sub, context.user_id
            )

        return LineItemResult(
            success=True,
            data={
                "subscription_id": str(subscription.id),
                "tokens_credited": tokens_credited,
            },
        )

    def _publish_subscription_event(
        self, event_name: str, subscription, user_id
    ) -> None:
        """Publish a subscription lifecycle event to EventBus."""
        try:
            from vbwd.events.bus import event_bus

            plan = subscription.tarif_plan
            event_bus.publish(
                event_name,
                {
                    "subscription_id": str(subscription.id),
                    "user_id": str(user_id),
                    "plan_id": str(plan.id) if plan else None,
                    "plan_slug": plan.slug if plan else None,
                    "plan_name": plan.name if plan else None,
                },
            )
        except Exception as publish_error:
            logger.warning(
                "[subscription] Failed to publish %s: %s",
                event_name,
                publish_error,
            )

    def _credit_plan_default_tokens(
        self, subscription, context: LineItemContext
    ) -> int:
        features = subscription.tarif_plan.features or {}
        default_tokens = (
            features.get("default_tokens", 0) if isinstance(features, dict) else 0
        )
        if default_tokens <= 0:
            return 0

        from vbwd.models.user_token_balance import UserTokenBalance, TokenTransaction

        token_repo = self._container.token_balance_repository()
        token_transaction_repo = self._container.token_transaction_repository()

        balance = token_repo.find_by_user_id(context.user_id)
        if not balance:
            balance = UserTokenBalance(id=uuid4(), user_id=context.user_id, balance=0)
        balance.balance += default_tokens
        token_repo.save(balance)

        transaction = TokenTransaction(
            id=uuid4(),
            user_id=context.user_id,
            amount=default_tokens,
            transaction_type=TokenTransactionType.SUBSCRIPTION,
            reference_id=subscription.id,
            description=f"Plan tokens: {subscription.tarif_plan.name}",
        )
        token_transaction_repo.save(transaction)

        return default_tokens

    def _activate_addon(self, line_item, context: LineItemContext) -> LineItemResult:
        addon_sub_repo = self._container.addon_subscription_repository()
        addon_subscription = addon_sub_repo.find_by_id(line_item.item_id)
        if (
            not addon_subscription
            or addon_subscription.status != SubscriptionStatus.PENDING
        ):
            return LineItemResult(success=True, data={})

        addon_subscription.status = SubscriptionStatus.ACTIVE
        addon_subscription.activated_at = utcnow()
        addon_sub_repo.save(addon_subscription)

        return LineItemResult(
            success=True,
            data={"addon_subscription_id": str(addon_subscription.id)},
        )

    # ── Reversal (refund) ─────────────────────────────────────────────────

    def _reverse_subscription(
        self, line_item, context: LineItemContext
    ) -> LineItemResult:
        subscription_repo = self._container.subscription_repository()
        subscription = subscription_repo.find_by_id(line_item.item_id)
        if not subscription or subscription.status != SubscriptionStatus.ACTIVE:
            return LineItemResult(success=True, data={})

        tokens_debited = 0
        if subscription.tarif_plan:
            features = subscription.tarif_plan.features or {}
            default_tokens = (
                features.get("default_tokens", 0) if isinstance(features, dict) else 0
            )
            if default_tokens > 0:
                token_service = self._container.token_service()
                token_service.debit_tokens(
                    user_id=context.user_id,
                    amount=default_tokens,
                    transaction_type=TokenTransactionType.REFUND,
                    reference_id=subscription.id,
                    description=f"Refund plan tokens: {subscription.tarif_plan.name}",
                )
                tokens_debited = default_tokens

        subscription.status = SubscriptionStatus.CANCELLED
        subscription.cancelled_at = utcnow()
        subscription_repo.save(subscription)

        # Publish subscription.cancelled to EventBus for plugin handlers
        self._publish_subscription_event(
            "subscription.cancelled",
            subscription,
            context.user_id,
        )

        return LineItemResult(
            success=True,
            data={
                "subscription_id": str(subscription.id),
                "tokens_debited": tokens_debited,
            },
        )

    def _reverse_addon(self, line_item, context: LineItemContext) -> LineItemResult:
        addon_sub_repo = self._container.addon_subscription_repository()
        addon_subscription = addon_sub_repo.find_by_id(line_item.item_id)
        if (
            not addon_subscription
            or addon_subscription.status != SubscriptionStatus.ACTIVE
        ):
            return LineItemResult(success=True, data={})

        addon_subscription.status = SubscriptionStatus.CANCELLED
        addon_subscription.cancelled_at = utcnow()
        addon_sub_repo.save(addon_subscription)

        return LineItemResult(
            success=True,
            data={"addon_subscription_id": str(addon_subscription.id)},
        )

    # ── Restoration (refund reversal) ─────────────────────────────────────

    def _restore_subscription(
        self, line_item, context: LineItemContext
    ) -> LineItemResult:
        subscription_repo = self._container.subscription_repository()
        subscription = subscription_repo.find_by_id(line_item.item_id)
        if not subscription or subscription.status != SubscriptionStatus.CANCELLED:
            return LineItemResult(success=True, data={})

        subscription.status = SubscriptionStatus.ACTIVE
        subscription.cancelled_at = None

        if subscription.tarif_plan:
            from plugins.subscription.subscription.services.subscription_service import (
                SubscriptionService,
            )

            period_days = SubscriptionService.PERIOD_DAYS.get(
                subscription.tarif_plan.billing_period, 30
            )
            subscription.starts_at = utcnow()
            subscription.expires_at = utcnow() + timedelta(days=period_days)

        subscription_repo.save(subscription)

        return LineItemResult(
            success=True,
            data={"subscription_id": str(subscription.id)},
        )

    def _restore_addon(self, line_item, context: LineItemContext) -> LineItemResult:
        addon_sub_repo = self._container.addon_subscription_repository()
        addon_subscription = addon_sub_repo.find_by_id(line_item.item_id)
        if (
            not addon_subscription
            or addon_subscription.status != SubscriptionStatus.CANCELLED
        ):
            return LineItemResult(success=True, data={})

        addon_subscription.status = SubscriptionStatus.ACTIVE
        addon_subscription.cancelled_at = None
        addon_subscription.activated_at = utcnow()
        addon_sub_repo.save(addon_subscription)

        return LineItemResult(
            success=True,
            data={"addon_subscription_id": str(addon_subscription.id)},
        )
