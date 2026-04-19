"""Subscription access level handler — auto-assigns user access levels on subscription events.

Listens to EventBus events:
- subscription.activated → assign plan-linked access level
- subscription.cancelled → revoke plan-linked level, assign fallback
"""
import logging
from uuid import UUID

logger = logging.getLogger(__name__)

FALLBACK_LEVEL_SLUG = "logged-in"


class SubscriptionAccessLevelHandler:
    """Assigns/revokes user access levels based on subscription lifecycle events.

    This handler lives in the subscription plugin (NOT core).
    Core provides the UserAccessLevelService; this handler uses it.
    """

    def on_subscription_activated(self, event_name: str, payload: dict) -> None:
        """Handle subscription.activated: assign the plan-linked access level."""
        user_id_str = payload.get("user_id")
        plan_slug = payload.get("plan_slug")
        if not user_id_str or not plan_slug:
            logger.debug(
                "[access-level] Skipping activated — missing user_id or plan_slug"
            )
            return

        try:
            from vbwd.services.user_access_level_service import (
                UserAccessLevelService,
            )

            service = UserAccessLevelService()
            user_id = UUID(user_id_str)

            # Find access level linked to this plan
            level = service.find_by_linked_plan_slug(plan_slug)
            if not level:
                logger.debug(
                    "[access-level] No access level linked to plan '%s'",
                    plan_slug,
                )
                return

            service.assign(user_id, level.id)
            logger.info(
                "[access-level] Assigned level '%s' to user %s (plan: %s)",
                level.slug,
                user_id_str,
                plan_slug,
            )

        except Exception as error:
            logger.warning(
                "[access-level] Failed to assign level on activation: %s", error
            )

    def on_subscription_cancelled(self, event_name: str, payload: dict) -> None:
        """Handle subscription.cancelled: revoke plan-linked level, assign fallback."""
        user_id_str = payload.get("user_id")
        plan_slug = payload.get("plan_slug")
        if not user_id_str or not plan_slug:
            logger.debug(
                "[access-level] Skipping cancelled — missing user_id or plan_slug"
            )
            return

        try:
            from vbwd.services.user_access_level_service import (
                UserAccessLevelService,
            )

            service = UserAccessLevelService()
            user_id = UUID(user_id_str)

            # Revoke all levels linked to this plan
            revoked_count = service.revoke_plan_linked_levels(user_id, plan_slug)

            # Assign fallback level ("logged-in") if any were revoked
            if revoked_count > 0:
                fallback_level = service.find_by_slug(FALLBACK_LEVEL_SLUG)
                if fallback_level:
                    service.assign(user_id, fallback_level.id)
                    logger.info(
                        "[access-level] Revoked %d level(s) for plan '%s', "
                        "assigned fallback '%s' to user %s",
                        revoked_count,
                        plan_slug,
                        FALLBACK_LEVEL_SLUG,
                        user_id_str,
                    )
                else:
                    logger.warning(
                        "[access-level] Fallback level '%s' not found",
                        FALLBACK_LEVEL_SLUG,
                    )

        except Exception as error:
            logger.warning(
                "[access-level] Failed to handle cancellation: %s", error
            )
