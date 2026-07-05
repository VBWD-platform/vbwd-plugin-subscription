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
        """Handle subscription.activated: assign access levels.

        Two independent sources are granted:
        1. the access level linked to the plan via ``linked_plan_slug`` (legacy);
        2. every access level the plan declares in its Features field
           (``access_levels: premium, vip``) — this ALWAYS runs, even when the
           plan has no ``linked_plan_slug`` level (the former early-return case).
        """
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

            # 1) Plan-linked access level (legacy ``linked_plan_slug``).
            level = service.find_by_linked_plan_slug(plan_slug)
            if level:
                service.assign(user_id, level.id)
                logger.info(
                    "[access-level] Assigned level '%s' to user %s (plan: %s)",
                    level.slug,
                    user_id_str,
                    plan_slug,
                )
            else:
                logger.debug(
                    "[access-level] No access level linked to plan '%s'",
                    plan_slug,
                )

            # 2) Features-declared access levels — always runs.
            self._plan_feature_service().grant_for_plan(user_id, payload.get("plan_id"))

            self._commit()

        except Exception as error:
            logger.warning(
                "[access-level] Failed to assign level on activation: %s", error
            )

    def on_subscription_ended(self, event_name: str, payload: dict) -> None:
        """Handle end-of-subscription (cancelled OR expired): revoke levels.

        Both ``subscription.cancelled`` and ``subscription.expired`` carry the
        same ``{user_id, plan_id, plan_slug, ...}`` payload (built by
        ``publish_subscription_event``), so a single revoke path serves both.
        Revocation is overlap-safe against the user's OTHER still-active plans.
        """
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

            # Revoke Features-declared access levels — overlap-safe against the
            # user's OTHER still-active plans.
            self._plan_feature_service().revoke_for_cancelled_plan(
                user_id, payload.get("plan_id")
            )

            self._commit()

        except Exception as error:
            logger.warning("[access-level] Failed to handle cancellation: %s", error)

    def on_subscription_cancelled(self, event_name: str, payload: dict) -> None:
        """Back-compat alias for ``subscription.cancelled`` (delegates to the
        neutral end-of-subscription revoke path)."""
        self.on_subscription_ended(event_name, payload)

    def _plan_feature_service(self):
        from plugins.subscription.subscription.services.plan_feature_access_level_service import (  # noqa: E501
            PlanFeatureAccessLevelService,
        )

        return PlanFeatureAccessLevelService()

    def _commit(self) -> None:
        """Commit this handler's own writes.

        The handler runs in an EventBus callback (invoice.paid → activate); the
        request teardown rolls back a flush-only session, so plugin writes must
        be committed here (mirrors ``PermissionSyncHandler``).
        """
        from vbwd.extensions import db

        db.session.commit()
