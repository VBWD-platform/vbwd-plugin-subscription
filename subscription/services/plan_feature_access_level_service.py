"""Grant/revoke the user access levels a plan declares in its Features field.

Admins add ONE line to a plan's "Features (one per line)" field:

    access_levels: premium, vip

The fe-admin parser stores this as ``TarifPlan.features == {"access_levels":
"premium, vip"}`` (a scalar STRING — the colon branch coerces the value). Other
Features lines stay plain marketing bullets (a list, or other dict keys) and are
ignored here. On activation each named slug maps to one core ``AccessLevel`` the
user gains; on cancellation the user loses it — but only if no OTHER still-active
plan of theirs declares the same slug (overlap-safe).

Pure orchestration over the core ``UserAccessLevelService`` write surface + the
plugin's own ``SubscriptionReadModel`` — it names no core RBAC internals (DIP)
and is fully unit-testable without the event bus (collaborators are injectable).
"""
import logging
import re
from typing import List, Set
from uuid import UUID

logger = logging.getLogger(__name__)

ACCESS_LEVELS_FEATURE_KEY = "access_levels"
_SLUG_SPLIT_PATTERN = re.compile(r"[,\s]+")


class PlanFeatureAccessLevelService:
    """Assign/revoke the access levels a plan declares in its Features field."""

    def __init__(self, access_level_service=None, read_model=None, session=None):
        # Injectable collaborators keep this service cheap to construct and easy
        # to stub; production resolves them lazily off the live ``db.session``.
        self._injected_access_level_service = access_level_service
        self._injected_read_model = read_model
        self._injected_session = session

    # ── parsing ──────────────────────────────────────────────────────────────

    @staticmethod
    def parse_access_level_slugs(features) -> List[str]:
        """Extract the declared access-level slugs from a plan's ``features``.

        Returns ``[]`` unless ``features`` is a dict with a truthy
        ``access_levels`` key. A list/tuple value is used directly; a string
        value is split on commas and/or whitespace. Slugs are trimmed, non-empty,
        de-duplicated and order-stable.
        """
        if not isinstance(features, dict):
            return []
        raw_value = features.get(ACCESS_LEVELS_FEATURE_KEY)
        if not raw_value:
            return []
        if isinstance(raw_value, (list, tuple)):
            candidates = [str(item) for item in raw_value]
        else:
            candidates = _SLUG_SPLIT_PATTERN.split(str(raw_value))

        slugs: List[str] = []
        for candidate in candidates:
            slug = candidate.strip()
            if slug and slug not in slugs:
                slugs.append(slug)
        return slugs

    # ── grant / revoke ───────────────────────────────────────────────────────

    def grant_for_plan(self, user_id: UUID, plan_id) -> None:
        """Assign every access level the plan declares to ``user_id``.

        Unknown slugs are logged and skipped (never raise). Assignment is a
        no-op when the user already holds the level (core service semantics).
        """
        plan = self._load_plan(plan_id)
        if plan is None:
            return
        access_level_service = self._access_level_service()
        for slug in self.parse_access_level_slugs(plan.features):
            level = access_level_service.find_by_slug(slug)
            if level is None:
                logger.warning(
                    "[plan-access-level] plan %s declares unknown access level "
                    "'%s' — skipping",
                    plan_id,
                    slug,
                )
                continue
            access_level_service.assign(user_id, level.id)

    def revoke_for_cancelled_plan(self, user_id: UUID, cancelled_plan_id) -> None:
        """Revoke the cancelled plan's declared levels, overlap-safe.

        A level is revoked only when NO other currently-active plan of the user
        (via ``SubscriptionReadModel.active_plan_ids``) also declares that slug.
        Levels no plan declares are left untouched.
        """
        cancelled_plan = self._load_plan(cancelled_plan_id)
        if cancelled_plan is None:
            return
        cancelled_slugs = self.parse_access_level_slugs(cancelled_plan.features)
        if not cancelled_slugs:
            return

        retained_slugs = self._slugs_from_other_active_plans(user_id, cancelled_plan_id)
        access_level_service = self._access_level_service()
        for slug in cancelled_slugs:
            if slug in retained_slugs:
                continue
            level = access_level_service.find_by_slug(slug)
            if level is None:
                continue
            access_level_service.revoke(user_id, level.id)

    def _slugs_from_other_active_plans(self, user_id, cancelled_plan_id) -> Set[str]:
        """Union of access-level slugs declared by the user's OTHER active plans."""
        cancelled_plan_id_str = str(cancelled_plan_id)
        retained_slugs: Set[str] = set()
        for plan_id in self._read_model().active_plan_ids(user_id):
            if str(plan_id) == cancelled_plan_id_str:
                continue
            plan = self._load_plan(plan_id)
            if plan is None:
                continue
            retained_slugs.update(self.parse_access_level_slugs(plan.features))
        return retained_slugs

    # ── collaborators ────────────────────────────────────────────────────────

    def _load_plan(self, plan_id):
        if plan_id is None:
            return None
        from plugins.subscription.subscription.models import TarifPlan

        return self._session().get(TarifPlan, self._as_uuid(plan_id))

    @staticmethod
    def _as_uuid(value) -> UUID:
        return value if isinstance(value, UUID) else UUID(str(value))

    def _session(self):
        if self._injected_session is not None:
            return self._injected_session
        from vbwd.extensions import db

        return db.session

    def _access_level_service(self):
        if self._injected_access_level_service is not None:
            return self._injected_access_level_service
        from vbwd.services.user_access_level_service import UserAccessLevelService

        return UserAccessLevelService(self._session())

    def _read_model(self):
        if self._injected_read_model is not None:
            return self._injected_read_model
        from plugins.subscription.subscription.services.subscription_read_model import (  # noqa: E501
            SubscriptionReadModel,
        )

        return SubscriptionReadModel()
