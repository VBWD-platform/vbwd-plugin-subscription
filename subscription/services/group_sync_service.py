"""Plan/add-on driven user-group reconciliation (S73 consumer).

``reconcile_user_groups(user_id)`` recomputes the user's MANAGED group
memberships from the union of their ACTIVE plans + add-ons and applies the diff
through the core ``IUserGroupMembership`` write port (D3 reconcile, not ad-hoc
add/remove). Each plan/add-on may declare ``user_checkin_group`` and/or
``user_checkout_group`` (a slug or list of slugs) in its ``features`` / ``config``.

D4 reconcile semantics (managed-only, check-out wins):
  * managed groups = every slug any of the user's sources mentions (check-in OR
    -out), regardless of the source's status — so a group stays managed while
    any source references it, and the LAST source's cancellation still removes
    the managed membership;
  * for each managed group: desired = (some ACTIVE source checks-in) AND NOT
    (any ACTIVE source checks-out) — check-out takes precedence;
  * un-managed memberships (groups no source ever mentions) are never touched —
    they stay admin-controlled.

Pure orchestration over the core port + the plugin's own read model — imports
NO core ``UserGroup`` model (DIP; core stays agnostic).
"""
import logging
from typing import List, Set, Tuple
from uuid import UUID

logger = logging.getLogger(__name__)

CHECKIN_GROUP_KEY = "user_checkin_group"
CHECKOUT_GROUP_KEY = "user_checkout_group"


class GroupSyncService:
    """Reconcile a user's managed group memberships against active sources."""

    def __init__(self, membership=None, read_model=None):
        self._injected_membership = membership
        self._injected_read_model = read_model

    def reconcile_user_groups(self, user_id) -> None:
        user_id = self._as_uuid(user_id)
        membership = self._membership()
        read_model = self._read_model()

        # Managed = every group ANY source (any status) references — keeps a
        # group managed while a now-cancelled source still mentions it.
        managed_slugs = self._slugs_for(
            self._all_plans(read_model, user_id),
            self._all_addons(read_model, user_id),
            CHECKIN_GROUP_KEY,
            CHECKOUT_GROUP_KEY,
        )
        if not managed_slugs:
            # No source ever mentions any group — leave admin memberships alone.
            return

        # Desired check-in / check-out come from ACTIVE sources only.
        active_checkin, active_checkout = self._active_checkin_checkout(
            read_model, user_id
        )

        current = membership.list_user_group_slugs(user_id)

        for slug in managed_slugs:
            # check-out wins: a checked-out group is removed even if also
            # checked in by another active source.
            should_be_member = slug in active_checkin and slug not in active_checkout
            if should_be_member and slug not in current:
                membership.add(user_id, slug)
            elif not should_be_member and slug in current:
                membership.remove(user_id, slug)

    # ── desired-state computation ────────────────────────────────────────────

    def _active_checkin_checkout(
        self, read_model, user_id: UUID
    ) -> Tuple[Set[str], Set[str]]:
        """Return ``(checkin_slugs, checkout_slugs)`` across ACTIVE sources."""
        checkin_slugs: Set[str] = set()
        checkout_slugs: Set[str] = set()
        for plan in self._active_plans(read_model, user_id):
            self._collect_source(plan.features, checkin_slugs, checkout_slugs)
        for addon in self._active_addons(read_model, user_id):
            self._collect_source(addon.config, checkin_slugs, checkout_slugs)
        return checkin_slugs, checkout_slugs

    def _slugs_for(self, plans, addons, *keys) -> Set[str]:
        """Union of every slug referenced under ``keys`` by the given sources."""
        slugs: Set[str] = set()
        for source_config in [plan.features for plan in plans] + [
            addon.config for addon in addons
        ]:
            config = source_config if isinstance(source_config, dict) else {}
            for key in keys:
                slugs.update(self._slug_list(config, key))
        return slugs

    def _collect_source(
        self, config, checkin_slugs: Set[str], checkout_slugs: Set[str]
    ) -> None:
        config = config if isinstance(config, dict) else {}
        checkin_slugs.update(self._slug_list(config, CHECKIN_GROUP_KEY))
        checkout_slugs.update(self._slug_list(config, CHECKOUT_GROUP_KEY))

    @staticmethod
    def _slug_list(config: dict, key: str) -> List[str]:
        raw = config.get(key)
        if not raw:
            return []
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, (list, tuple)):
            return [str(item) for item in raw if item]
        return []

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

    def _all_plans(self, read_model, user_id: UUID) -> List:
        from plugins.subscription.subscription.models import TarifPlan

        plans = []
        for plan_id in read_model.all_plan_ids(user_id):
            plan = self._session().get(TarifPlan, plan_id)
            if plan is not None:
                plans.append(plan)
        return plans

    def _all_addons(self, read_model, user_id: UUID) -> List:
        from plugins.subscription.subscription.models import AddOn

        addons = []
        for addon_id in read_model.all_addon_ids(user_id):
            addon = self._session().get(AddOn, addon_id)
            if addon is not None:
                addons.append(addon)
        return addons

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _as_uuid(user_id) -> UUID:
        return user_id if isinstance(user_id, UUID) else UUID(str(user_id))

    def _session(self):
        from vbwd.extensions import db

        return db.session

    def _membership(self):
        if self._injected_membership is not None:
            return self._injected_membership
        from vbwd.services.user_group_membership import (
            resolve_user_group_membership,
        )

        return resolve_user_group_membership()

    def _read_model(self):
        if self._injected_read_model is not None:
            return self._injected_read_model
        from plugins.subscription.subscription.services.subscription_read_model import (  # noqa: E501
            SubscriptionReadModel,
        )

        return SubscriptionReadModel()
