"""Unit tests for subscription demo_seed user-access-level seeding (S39 §3.5).

``seed_catalog`` idempotently creates two plan-linked ``vbwd_user_access_level``
rows (``basic`` + ``pro``) through the core ``UserAccessLevelService`` — never
raw SQL. Re-running creates nothing new. The slugs are sourced from
``DEMO_PLANS`` (DRY), co-located with the plans they link to.
"""
from unittest.mock import MagicMock, patch

import pytest

from plugins.subscription.subscription import demo_seed


@pytest.fixture()
def fake_session():
    return MagicMock()


def _service_finding(existing_by_plan_slug):
    """Build a patched UserAccessLevelService whose ``find_by_linked_plan_slug``
    answers from the supplied dict."""
    service = MagicMock()
    service.find_by_linked_plan_slug.side_effect = (
        lambda plan_slug: existing_by_plan_slug.get(plan_slug)
    )
    return service


class TestSeedCatalogUserAccessLevels:
    def test_seed_catalog_creates_basic_and_pro_linked_levels(self, fake_session):
        """seed_catalog creates two access levels linked to basic + pro plans."""
        from vbwd.models.user_access_level import UserAccessLevel

        service = _service_finding({})
        with patch(
            "vbwd.services.user_access_level_service.UserAccessLevelService",
            return_value=service,
        ), patch("plugins.subscription.subscription.models.TarifPlan"), patch(
            "plugins.subscription.subscription.models.AddOn"
        ):
            demo_seed.seed_catalog(fake_session)

        added = [call.args[0] for call in fake_session.add.call_args_list]
        access_levels = [obj for obj in added if isinstance(obj, UserAccessLevel)]
        linked_plan_slugs = {level.linked_plan_slug for level in access_levels}
        names = {level.name for level in access_levels}
        assert linked_plan_slugs == {"basic", "pro"}
        assert names == {"Basic", "Pro"}

    def test_idempotent_second_run_creates_none(self, fake_session):
        """When both levels already exist, the seeder creates nothing new."""
        existing = {
            "basic": MagicMock(linked_plan_slug="basic"),
            "pro": MagicMock(linked_plan_slug="pro"),
        }
        service = _service_finding(existing)
        with patch(
            "vbwd.services.user_access_level_service.UserAccessLevelService",
            return_value=service,
        ):
            created = demo_seed.seed_user_access_levels(fake_session)

        assert created == 0
        fake_session.add.assert_not_called()

    def test_existence_checked_via_find_by_linked_plan_slug(self, fake_session):
        """Existence is resolved via UserAccessLevelService.find_by_linked_plan_slug."""
        service = _service_finding({})
        with patch(
            "vbwd.services.user_access_level_service.UserAccessLevelService",
            return_value=service,
        ) as service_cls:
            created = demo_seed.seed_user_access_levels(fake_session)

        assert created == 2
        service_cls.assert_called_once_with(fake_session)
        consulted = {
            call.args[0] for call in service.find_by_linked_plan_slug.call_args_list
        }
        assert consulted == {"basic", "pro"}

    def test_partial_state_creates_only_missing(self, fake_session):
        """When only basic exists, the seeder creates just pro (idempotency)."""
        existing = {"basic": MagicMock(linked_plan_slug="basic")}
        service = _service_finding(existing)
        with patch(
            "vbwd.services.user_access_level_service.UserAccessLevelService",
            return_value=service,
        ):
            created = demo_seed.seed_user_access_levels(fake_session)

        assert created == 1
        added_levels = [call.args[0] for call in fake_session.add.call_args_list]
        assert [level.linked_plan_slug for level in added_levels] == ["pro"]

    def test_slugs_sourced_from_demo_plans(self):
        """The seeded slugs are real DEMO_PLANS slugs (DRY — single source)."""
        plan_slugs = {plan["slug"] for plan in demo_seed.DEMO_PLANS}
        assert set(demo_seed.USER_ACCESS_LEVEL_PLAN_SLUGS) <= plan_slugs
        assert set(demo_seed.USER_ACCESS_LEVEL_PLAN_SLUGS) == {"basic", "pro"}
