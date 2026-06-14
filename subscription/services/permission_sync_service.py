"""Plan/add-on driven permission reconciliation (S69 consumer).

``reconcile_user(user_id)`` recomputes the user's desired permission grants from
the union of their ACTIVE/TRIALING plans + add-ons and diffs against what is
assigned (D1: reconcile, not ad-hoc grant). Each plan/add-on that declares
``permissions_enable`` / ``special_permissions_enable`` maps to one managed,
source-scoped ``UserAccessLevel`` (and, when D4 allows, one managed ``Role``)
with deterministic slug ``auto-plan-<slug>`` / ``auto-addon-<slug>`` (D2).

This service is pure orchestration over the core ``IUserPermissionGrant`` write
port + the plugin's own read model — it imports **no** core RBAC models (DIP).
"""
import logging
from typing import Dict, List, Tuple
from uuid import UUID

logger = logging.getLogger(__name__)

PLAN_LEVEL_SLUG_PREFIX = "auto-plan-"
ADDON_LEVEL_SLUG_PREFIX = "auto-addon-"
MANAGED_SLUG_PREFIXES = (PLAN_LEVEL_SLUG_PREFIX, ADDON_LEVEL_SLUG_PREFIX)

USER_PERMISSIONS_KEY = "permissions_enable"
SPECIAL_PERMISSIONS_KEY = "special_permissions_enable"


class PermissionSyncService:
    """Reconcile a user's plan/add-on permissions against their active sources."""

    def __init__(self, grant=None, read_model=None):
        # Resolve the core port and the plugin read model lazily so the service
        # is cheap to construct and easy to stub in tests.
        self._injected_grant = grant
        self._injected_read_model = read_model

    def reconcile_user(self, user_id) -> None:
        user_id = self._as_uuid(user_id)
        grant = self._grant()

        desired_levels, desired_roles = self._desired_managed_entities(user_id)

        # 1) Ensure + assign every managed entity for an active source.
        for slug, (name, permission_names, linked_plan_slug) in desired_levels.items():
            level_id = grant.ensure_user_access_level(
                slug,
                name,
                permission_names,
                linked_plan_slug=linked_plan_slug,
            )
            grant.assign_level(user_id, level_id)

        for slug, (name, permission_names) in desired_roles.items():
            role_id = grant.ensure_role(slug, name, permission_names)
            if role_id is not None:
                grant.assign_role(user_id, slug)

        # 2) Revoke managed entities the user still holds but no longer earns.
        for slug, level_id in grant.list_assigned_levels(user_id).items():
            if self._is_managed(slug) and slug not in desired_levels:
                grant.revoke_level(user_id, level_id)

        for slug in grant.list_assigned_roles(user_id):
            if self._is_managed(slug) and slug not in desired_roles:
                grant.revoke_role(user_id, slug)

    # ── desired-state computation ────────────────────────────────────────────

    def _desired_managed_entities(
        self, user_id: UUID
    ) -> Tuple[Dict[str, Tuple], Dict[str, Tuple]]:
        """Build the desired managed levels/roles from the user's active sources.

        Returns ``(levels, roles)`` where
          levels: slug -> (name, permission_names, linked_plan_slug)
          roles:  slug -> (name, permission_names)
        """
        levels: Dict[str, Tuple] = {}
        roles: Dict[str, Tuple] = {}

        read_model = self._read_model()

        for plan in self._active_plans(read_model, user_id):
            self._collect_source(
                slug_prefix=PLAN_LEVEL_SLUG_PREFIX,
                source_slug=plan.slug,
                name=plan.name,
                config=plan.features,
                linked_plan_slug=plan.slug,
                levels=levels,
                roles=roles,
            )

        for addon in self._active_addons(read_model, user_id):
            self._collect_source(
                slug_prefix=ADDON_LEVEL_SLUG_PREFIX,
                source_slug=addon.slug,
                name=addon.name,
                config=addon.config,
                linked_plan_slug=None,
                levels=levels,
                roles=roles,
            )

        return levels, roles

    def _collect_source(
        self,
        *,
        slug_prefix: str,
        source_slug: str,
        name: str,
        config,
        linked_plan_slug,
        levels: Dict[str, Tuple],
        roles: Dict[str, Tuple],
    ) -> None:
        config = config if isinstance(config, dict) else {}
        user_permissions = self._permission_list(config, USER_PERMISSIONS_KEY)
        special_permissions = self._permission_list(config, SPECIAL_PERMISSIONS_KEY)

        managed_slug = f"{slug_prefix}{source_slug}"
        if user_permissions:
            levels[managed_slug] = (
                f"Auto: {name}",
                user_permissions,
                linked_plan_slug,
            )
        if special_permissions:
            roles[managed_slug] = (f"Auto: {name}", special_permissions)

    @staticmethod
    def _permission_list(config: dict, key: str) -> List[str]:
        raw = config.get(key) or []
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(item) for item in raw]

    # ── active-source reads (plugin-internal) ────────────────────────────────

    def _active_plans(self, read_model, user_id: UUID) -> List:
        from plugins.subscription.subscription.models import TarifPlan

        plans = []
        for plan_id in read_model.active_plan_ids(user_id):
            plan = self._session().get(TarifPlan, plan_id)
            if plan is not None:
                plans.append(plan)
        return plans

    def _active_addons(self, read_model, user_id: UUID) -> List:
        from plugins.subscription.subscription.models import AddOn

        addons = []
        for addon_id in read_model.active_addon_ids(user_id):
            addon = self._session().get(AddOn, addon_id)
            if addon is not None:
                addons.append(addon)
        return addons

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _is_managed(slug: str) -> bool:
        return any(slug.startswith(prefix) for prefix in MANAGED_SLUG_PREFIXES)

    @staticmethod
    def _as_uuid(user_id) -> UUID:
        return user_id if isinstance(user_id, UUID) else UUID(str(user_id))

    def _session(self):
        from vbwd.extensions import db

        return db.session

    def _grant(self):
        if self._injected_grant is not None:
            return self._injected_grant
        from vbwd.services.user_permission_grant import (
            resolve_user_permission_grant,
        )

        return resolve_user_permission_grant()

    def _read_model(self):
        if self._injected_read_model is not None:
            return self._injected_read_model
        from plugins.subscription.subscription.services.subscription_read_model import (  # noqa: E501
            SubscriptionReadModel,
        )

        return SubscriptionReadModel()
