"""EventBus handler that reconciles a user's group memberships (S73).

Subscribed to every subscription/add-on lifecycle event; each one triggers the
same idempotent ``GroupSyncService.reconcile_user_groups(user_id)`` (D3). Lives
in the subscription plugin (not core); reaches user-groups only through the core
``IUserGroupMembership`` port.
"""
import logging
from uuid import UUID

logger = logging.getLogger(__name__)


class GroupSyncHandler:
    """Reconcile group memberships on any subscription/add-on lifecycle event."""

    def on_lifecycle_event(self, event_name: str, payload: dict) -> None:
        user_id = (payload or {}).get("user_id")
        if not user_id:
            logger.debug("[group-sync] skipping %s — no user_id in payload", event_name)
            return

        try:
            from plugins.subscription.subscription.services.group_sync_service import (  # noqa: E501
                GroupSyncService,
            )
            from vbwd.extensions import db

            GroupSyncService().reconcile_user_groups(UUID(str(user_id)))
            db.session.commit()
            logger.info(
                "[group-sync] reconciled groups for user %s on %s",
                user_id,
                event_name,
            )
        except Exception as error:
            logger.warning(
                "[group-sync] failed to reconcile user %s on %s: %s",
                user_id,
                event_name,
                error,
            )
