"""Tests for the subscription plugin's demo/test data hooks.

Behaviour relocated from core seeders (Sprint 03/S5b) — coverage moves
with the code (E2): demo plans/addons, the marker test plan, and the
active subscription for the test user.
"""
from unittest.mock import MagicMock, patch

from plugins.subscription.subscription import demo_seed


def test_seed_catalog_adds_all_demo_plans_and_addons():
    session = MagicMock()
    # No access level exists yet → all plan-linked levels are created too.
    service = MagicMock()
    service.find_by_linked_plan_slug.return_value = None
    with patch.object(demo_seed, "DEMO_PLANS", demo_seed.DEMO_PLANS), patch(
        "plugins.subscription.subscription.models.TarifPlan"
    ), patch("plugins.subscription.subscription.models.AddOn"), patch(
        "vbwd.services.user_access_level_service.UserAccessLevelService",
        return_value=service,
    ):
        demo_seed.seed_catalog(session)

    expected = (
        len(demo_seed.DEMO_PLANS)
        + len(demo_seed.DEMO_ADDONS)
        + len(demo_seed.USER_ACCESS_LEVEL_PLAN_SLUGS)
    )
    assert session.add.call_count == expected


def test_test_plan_uses_marker_and_known_slug():
    assert demo_seed.TEST_PLAN_SLUG == "test-data-basic-plan"
    assert demo_seed.TEST_DATA_MARKER == "TEST_DATA_"


def test_seed_test_data_creates_plan_and_subscription_for_new_user():
    session = MagicMock()
    # No existing plan, no existing subscription.
    session.query.return_value.filter_by.return_value.first.return_value = None
    test_user = MagicMock(id="user-uuid")

    with patch("plugins.subscription.subscription.models.TarifPlan"), patch(
        "plugins.subscription.subscription.models.Subscription"
    ):
        demo_seed.seed_test_data(session, test_user)

    # plan + subscription both added
    assert session.add.call_count == 2


def test_seed_test_data_skips_when_subscription_exists():
    session = MagicMock()
    existing_plan = MagicMock()
    existing_sub = MagicMock()
    # 1st query: plan lookup → existing; 2nd: subscription lookup → existing
    session.query.return_value.filter_by.return_value.first.side_effect = [
        existing_plan,
        existing_sub,
    ]
    test_user = MagicMock(id="user-uuid")

    with patch("plugins.subscription.subscription.models.TarifPlan"), patch(
        "plugins.subscription.subscription.models.Subscription"
    ):
        demo_seed.seed_test_data(session, test_user)

    # plan existed and subscription existed → nothing added
    assert session.add.call_count == 0


def test_clean_test_data_deletes_subscriptions_and_test_plan():
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = [
        MagicMock(id="u1")
    ]

    with patch("plugins.subscription.subscription.models.TarifPlan"), patch(
        "plugins.subscription.subscription.models.Subscription"
    ), patch("vbwd.models.user.User"):
        demo_seed.clean_test_data(session)

    # at least the per-user subscription delete + the test-plan delete ran
    assert session.query.return_value.filter_by.return_value.delete.called
    assert session.query.return_value.filter.return_value.delete.called
