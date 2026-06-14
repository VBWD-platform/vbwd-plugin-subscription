"""EventBus handler that reconciles a user's plan/add-on permissions (S69).

Subscribed to every subscription/add-on lifecycle event; each one triggers the
same idempotent ``PermissionSyncService.reconcile_user(user_id)`` (D1). Lives in
the subscription plugin (not core); reaches RBAC only through the core port.
"""
import logging
from uuid import UUID

logger = logging.getLogger(__name__)


class PermissionSyncHandler:
    """Reconcile permissions on any subscription/add-on lifecycle event."""

    def on_lifecycle_event(self, event_name: str, payload: dict) -> None:
        user_id = (payload or {}).get("user_id")
        if not user_id:
            logger.debug(
                "[permission-sync] skipping %s — no user_id in payload", event_name
            )
            return

        try:
            from plugins.subscription.subscription.services.permission_sync_service import (  # noqa: E501
                PermissionSyncService,
            )
            from vbwd.extensions import db

            PermissionSyncService().reconcile_user(UUID(str(user_id)))
            db.session.commit()
            logger.info(
                "[permission-sync] reconciled user %s on %s", user_id, event_name
            )
        except Exception as error:
            logger.warning(
                "[permission-sync] failed to reconcile user %s on %s: %s",
                user_id,
                event_name,
                error,
            )
