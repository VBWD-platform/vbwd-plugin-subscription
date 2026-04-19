"""Subscription cancellation event handler."""
from vbwd.utils.datetime_utils import utcnow
from vbwd.events.domain import DomainEvent, EventResult, IEventHandler
from vbwd.events.payment_events import SubscriptionCancelledEvent
from vbwd.models.enums import SubscriptionStatus


class SubscriptionCancelledHandler(IEventHandler):
    """Handles subscription.cancelled events.

    Marks subscription as CANCELLED. Does NOT refund -- that's a separate flow.
    Also cancels linked add-on subscriptions.
    """

    def __init__(self, container):
        self._container = container

    def _get_services(self):
        return {
            "subscription": self._container.subscription_repository(),
            "addon_subscription": self._container.addon_subscription_repository(),
        }

    def can_handle(self, event: DomainEvent) -> bool:
        return isinstance(event, SubscriptionCancelledEvent)

    def handle(self, event: DomainEvent) -> EventResult:
        if not isinstance(event, SubscriptionCancelledEvent):
            return EventResult.error_result("Invalid event type")

        try:
            repos = self._get_services()
            subscription = repos["subscription"].find_by_id(event.subscription_id)
            if not subscription or subscription.status == SubscriptionStatus.CANCELLED:
                return EventResult.success_result()

            subscription.status = SubscriptionStatus.CANCELLED
            subscription.cancelled_at = utcnow()
            repos["subscription"].save(subscription)

            # Cancel linked add-on subscriptions
            addon_subs = repos["addon_subscription"].find_by_subscription(
                event.subscription_id
            )
            for addon_sub in addon_subs:
                if addon_sub.status == SubscriptionStatus.ACTIVE:
                    addon_sub.status = SubscriptionStatus.CANCELLED
                    addon_sub.cancelled_at = utcnow()
                    repos["addon_subscription"].save(addon_sub)

            return EventResult.success_result(
                {"subscription_id": str(event.subscription_id)}
            )

        except Exception as e:
            return EventResult.error_result(str(e))
