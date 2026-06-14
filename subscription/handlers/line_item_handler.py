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

    def resolve_catalog_item_id(self, line_item):
        """SUBSCRIPTION → its tarif plan id, ADD_ON → its addon id.

        None for any line item this plugin does not own.
        """
        from vbwd.extensions import db
        from plugins.subscription.subscription.models import (
            Subscription,
            AddOnSubscription,
        )

        if line_item.item_type == LineItemType.SUBSCRIPTION:
            subscription = db.session.get(Subscription, line_item.item_id)
            return str(subscription.tarif_plan_id) if subscription else None
        if line_item.item_type == LineItemType.ADD_ON:
            addon_subscription = db.session.get(AddOnSubscription, line_item.item_id)
            return str(addon_subscription.addon_id) if addon_subscription else None
        return None

    def resolve_catalog_entity_ref(self, line_item):
        """Source entity ref for the S77 invoice snapshot.

        SUBSCRIPTION → ``("tarif_plan", plan_id)``, ADD_ON → ``("addon", addon_id)``.
        ``None`` for any line item this plugin does not own — the catalog id is
        the same one ``resolve_catalog_item_id`` returns, paired with this
        plugin's registered entity type.
        """
        if line_item.item_type == LineItemType.SUBSCRIPTION:
            catalog_id = self.resolve_catalog_item_id(line_item)
            return ("tarif_plan", catalog_id) if catalog_id else None
        if line_item.item_type == LineItemType.ADD_ON:
            catalog_id = self.resolve_catalog_item_id(line_item)
            return ("addon", catalog_id) if catalog_id else None
        return None

    def is_recurring_line_item(self, line_item):
        """SUBSCRIPTION → plan.is_recurring, ADD_ON → addon.is_recurring.

        False for any line item this plugin does not own.
        """
        from vbwd.extensions import db
        from plugins.subscription.subscription.models import (
            Subscription,
            AddOnSubscription,
        )

        if line_item.item_type == LineItemType.SUBSCRIPTION:
            subscription = db.session.get(Subscription, line_item.item_id)
            return bool(
                subscription
                and subscription.tarif_plan
                and subscription.tarif_plan.is_recurring
            )
        if line_item.item_type == LineItemType.ADD_ON:
            addon_subscription = db.session.get(AddOnSubscription, line_item.item_id)
            return bool(
                addon_subscription
                and addon_subscription.addon
                and addon_subscription.addon.is_recurring
            )
        return False

    def recurring_billing_spec(self, line_item):
        """(name, billing_period) for recurring SUBSCRIPTION/ADD_ON items.

        None for one-off items or items this plugin does not own — so payment
        providers only set up recurring charges for genuinely recurring plans.
        """
        from vbwd.events.line_item_registry import RecurringBillingSpec
        from vbwd.extensions import db
        from plugins.subscription.subscription.models import (
            Subscription,
            AddOnSubscription,
        )

        if line_item.item_type == LineItemType.SUBSCRIPTION:
            subscription = db.session.get(Subscription, line_item.item_id)
            plan = subscription.tarif_plan if subscription else None
            if plan and plan.is_recurring:
                return RecurringBillingSpec(
                    name=plan.name, billing_period=plan.billing_period.value
                )
            return None
        if line_item.item_type == LineItemType.ADD_ON:
            addon_subscription = db.session.get(AddOnSubscription, line_item.item_id)
            addon = addon_subscription.addon if addon_subscription else None
            if addon and addon.is_recurring:
                return RecurringBillingSpec(
                    name=addon.name, billing_period=addon.billing_period
                )
            return None
        return None

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
        """Publish a subscription lifecycle event to EventBus (DRY: shared home)."""
        from plugins.subscription.subscription.services.lifecycle_events import (
            publish_subscription_event,
        )

        publish_subscription_event(event_name, subscription, user_id)

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

        self._publish_addon_event("addon.activated", addon_subscription)

        return LineItemResult(
            success=True,
            data={"addon_subscription_id": str(addon_subscription.id)},
        )

    def _publish_addon_event(self, event_name: str, addon_subscription) -> None:
        """Publish an add-on lifecycle event to EventBus (DRY: shared home)."""
        from plugins.subscription.subscription.services.lifecycle_events import (
            publish_addon_event,
        )

        publish_addon_event(event_name, addon_subscription)

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

        self._publish_addon_event("addon.cancelled", addon_subscription)

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
