"""Single home for subscription/add-on lifecycle EventBus publishes (S69 D5).

The lifecycle events were previously emitted only on the payment path (and
inline in ``line_item_handler``). S69 needs them to fire on *every* status
change (admin routes, scheduler expiry, add-on activate/cancel) so the
permission reconcile is reliable. Centralising the publish here keeps the
payload shape consistent (DRY) and lets every emit site reuse it.
"""
import logging

logger = logging.getLogger(__name__)

EVENT_SUBSCRIPTION_ACTIVATED = "subscription.activated"
EVENT_SUBSCRIPTION_CANCELLED = "subscription.cancelled"
EVENT_SUBSCRIPTION_EXPIRED = "subscription.expired"
EVENT_ADDON_ACTIVATED = "addon.activated"
EVENT_ADDON_CANCELLED = "addon.cancelled"


def publish_subscription_event(event_name: str, subscription, user_id) -> None:
    """Publish a subscription lifecycle event with a stable payload."""
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
            "[subscription] Failed to publish %s: %s", event_name, publish_error
        )


def publish_addon_event(event_name: str, addon_subscription) -> None:
    """Publish an add-on lifecycle event with ``{user_id, addon_id, addon_slug}``."""
    try:
        from vbwd.events.bus import event_bus

        addon = addon_subscription.addon
        event_bus.publish(
            event_name,
            {
                "user_id": str(addon_subscription.user_id),
                "addon_id": str(addon_subscription.addon_id),
                "addon_slug": addon.slug if addon else None,
            },
        )
    except Exception as publish_error:
        logger.warning(
            "[subscription] Failed to publish %s: %s", event_name, publish_error
        )
