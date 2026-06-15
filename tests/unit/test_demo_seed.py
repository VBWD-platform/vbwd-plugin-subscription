"""Tests for the subscription plugin's demo/test data hooks.

Behaviour relocated from core seeders (Sprint 03/S5b) — coverage moves
with the code (E2): demo plans/addons, the marker test plan, and the
active subscription for the test user.
"""
from unittest.mock import MagicMock, patch

from plugins.subscription.subscription import demo_seed


def test_seed_catalog_upserts_all_demo_plans_and_addons():
    """S88: ``seed_catalog`` is now an idempotent upsert by slug. On a clean DB
    (no existing plan/addon row) it inserts every demo plan + addon + access
    level — exactly the rows it owns."""
    session = MagicMock()
    # No plan/addon row exists yet → every slug is inserted.
    session.query.return_value.filter_by.return_value.first.return_value = None
    # No access level exists yet → all plan-linked levels are created too.
    service = MagicMock()
    service.find_by_linked_plan_slug.return_value = None
    with patch.object(demo_seed, "DEMO_PLANS", demo_seed.DEMO_PLANS), patch(
        "plugins.subscription.subscription.models.TarifPlan"
    ), patch("plugins.subscription.subscription.models.AddOn"), patch(
        "plugins.subscription.subscription.models.TarifPlanCategory"
    ), patch(
        "vbwd.services.user_access_level_service.UserAccessLevelService",
        return_value=service,
    ):
        demo_seed.seed_catalog(session)

    expected = (
        len(demo_seed.DEMO_PLANS)
        + len(demo_seed.DEMO_ADDONS)
        + len(demo_seed.USER_ACCESS_LEVEL_PLAN_SLUGS)
        + 1  # the root tarif category
    )
    assert session.add.call_count == expected


def test_seed_catalog_is_idempotent_when_rows_exist():
    """When every plan/addon slug already exists, ``seed_catalog`` updates in
    place and inserts no duplicate plan/addon rows (the registry contract)."""
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = MagicMock()
    service = MagicMock()
    # access levels already exist too → none added
    service.find_by_linked_plan_slug.return_value = MagicMock()
    with patch.object(demo_seed, "DEMO_PLANS", demo_seed.DEMO_PLANS), patch(
        "plugins.subscription.subscription.models.TarifPlan"
    ), patch("plugins.subscription.subscription.models.AddOn"), patch(
        "vbwd.services.user_access_level_service.UserAccessLevelService",
        return_value=service,
    ):
        demo_seed.seed_catalog(session)

    # Existing rows are updated in place — nothing is added.
    assert session.add.call_count == 0


def test_root_category_links_only_demo_subscription_plans():
    """The ``root`` tarif category links exactly the subscription demo plans
    (so ``/tarif-plans?category=root`` renders them) and excludes any plan that
    is not part of this seeder's demo set (e.g. a GHRM package)."""
    assert demo_seed.ROOT_CATEGORY_SLUG == "root"
    # The linked set is sourced from DEMO_PLANS — no second copy of the slugs.
    assert demo_seed.ROOT_CATEGORY_PLAN_SLUGS == [
        plan["slug"] for plan in demo_seed.DEMO_PLANS
    ]


def test_seed_catalog_creates_root_category_and_links_demo_plans():
    """On a clean DB, ``seed_catalog`` upserts the ``root`` category and links
    each demo subscription plan to it (idempotent by slug)."""
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = None
    service = MagicMock()
    service.find_by_linked_plan_slug.return_value = None

    created_category = MagicMock()
    created_category.tarif_plans = []

    with patch("plugins.subscription.subscription.models.TarifPlan") as plan_cls, patch(
        "plugins.subscription.subscription.models.AddOn"
    ), patch(
        "plugins.subscription.subscription.models.TarifPlanCategory",
        return_value=created_category,
    ) as category_cls, patch(
        "vbwd.services.user_access_level_service.UserAccessLevelService",
        return_value=service,
    ):
        # Each TarifPlan(...) instance is a distinct mock carrying its slug so we
        # can assert which plans get linked to the category.
        plan_cls.side_effect = lambda **kwargs: MagicMock(slug=kwargs.get("slug"))
        demo_seed.seed_catalog(session)

    # The root category was created with the root slug and named.
    category_cls.assert_called_once_with(slug=demo_seed.ROOT_CATEGORY_SLUG)
    assert created_category.name == demo_seed.ROOT_CATEGORY_NAME
    # Every demo plan (and only those) was linked to the category.
    linked_slugs = [plan.slug for plan in created_category.tarif_plans]
    assert linked_slugs == demo_seed.ROOT_CATEGORY_PLAN_SLUGS


def test_seed_catalog_root_category_link_is_idempotent():
    """Re-running ``seed_catalog`` when the ``root`` category already holds the
    demo plans does not duplicate the links."""
    session = MagicMock()
    service = MagicMock()
    service.find_by_linked_plan_slug.return_value = MagicMock()

    existing_plans = {
        plan["slug"]: MagicMock(slug=plan["slug"]) for plan in demo_seed.DEMO_PLANS
    }
    existing_category = MagicMock()
    existing_category.slug = demo_seed.ROOT_CATEGORY_SLUG
    existing_category.tarif_plans = list(existing_plans.values())

    def _filter_by(**kwargs):
        result = MagicMock()
        slug = kwargs.get("slug")
        if slug in existing_plans:
            result.first.return_value = existing_plans[slug]
        elif slug == demo_seed.ROOT_CATEGORY_SLUG:
            result.first.return_value = existing_category
        else:
            result.first.return_value = MagicMock(slug=slug)
        return result

    session.query.return_value.filter_by.side_effect = _filter_by

    with patch("plugins.subscription.subscription.models.TarifPlan"), patch(
        "plugins.subscription.subscription.models.AddOn"
    ), patch("plugins.subscription.subscription.models.TarifPlanCategory"), patch(
        "vbwd.services.user_access_level_service.UserAccessLevelService",
        return_value=service,
    ):
        demo_seed.seed_catalog(session)

    # No plan is linked twice — the category already holds exactly the demo set.
    assert len(existing_category.tarif_plans) == len(demo_seed.DEMO_PLANS)


def test_seed_catalog_invalidates_plan_and_addon_caches():
    """A fresh ``reset-demo`` must be immediately consistent: after reseeding,
    ``seed_catalog`` clears the TTL-cached public catalog so ``/tarif-plans*``
    and ``/addons/`` serve the freshly seeded rows instead of a stale body."""
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = None
    service = MagicMock()
    service.find_by_linked_plan_slug.return_value = None

    with patch("plugins.subscription.subscription.models.TarifPlan"), patch(
        "plugins.subscription.subscription.models.AddOn"
    ), patch("plugins.subscription.subscription.models.TarifPlanCategory"), patch(
        "vbwd.services.user_access_level_service.UserAccessLevelService",
        return_value=service,
    ), patch.object(
        demo_seed, "invalidate_plan_cache"
    ) as invalidate_plans, patch.object(
        demo_seed, "invalidate_addon_cache"
    ) as invalidate_addons:
        demo_seed.seed_catalog(session)

    invalidate_plans.assert_called_once_with()
    invalidate_addons.assert_called_once_with()


def test_seed_catalog_links_canonical_vat_to_plans_and_addons():
    """S85.4: after seeding, every demo plan + addon carries the canonical VAT
    so its price disclosure shows gross > net. The tax is looked up by code
    through the core linker (no cross-plugin import)."""
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = None
    service = MagicMock()
    service.find_by_linked_plan_slug.return_value = None

    def _make(**kwargs):
        return MagicMock(slug=kwargs.get("slug"), taxes=[])

    with patch(
        "plugins.subscription.subscription.models.TarifPlan", side_effect=_make
    ), patch(
        "plugins.subscription.subscription.models.AddOn", side_effect=_make
    ), patch(
        "plugins.subscription.subscription.models.TarifPlanCategory"
    ), patch(
        "vbwd.services.user_access_level_service.UserAccessLevelService",
        return_value=service,
    ), patch.object(
        demo_seed, "link_demo_tax"
    ) as link_demo_tax:
        demo_seed.seed_catalog(session)

    assert link_demo_tax.called
    linked_sellables = []
    for call in link_demo_tax.call_args_list:
        linked_sellables.extend(call.args[1])
    assert len(linked_sellables) == len(demo_seed.DEMO_PLANS) + len(
        demo_seed.DEMO_ADDONS
    )


def test_test_plan_uses_marker_and_known_slug():
    assert demo_seed.TEST_PLAN_SLUG == "test-data-basic-plan"
    assert demo_seed.TEST_DATA_MARKER == "TEST_DATA_"


def test_seed_test_data_creates_plan_subscription_and_invoice_for_new_user():
    session = MagicMock()
    # No existing plan, no existing subscription, no existing invoice.
    session.query.return_value.filter_by.return_value.first.return_value = None
    test_user = MagicMock(id="user-uuid")

    with patch("plugins.subscription.subscription.models.TarifPlan"), patch(
        "plugins.subscription.subscription.models.Subscription"
    ), patch("vbwd.models.invoice.UserInvoice"), patch(
        "vbwd.models.invoice_line_item.InvoiceLineItem"
    ):
        demo_seed.seed_test_data(session, test_user)

    # plan + subscription + invoice + subscription line item all added
    assert session.add.call_count == 4


def test_seed_test_data_seeds_invoice_when_subscription_exists_but_invoice_absent():
    session = MagicMock()
    existing_plan = MagicMock()
    existing_sub = MagicMock(id="sub-uuid")
    # 1st query: plan → existing; 2nd: subscription → existing; 3rd: invoice → None
    session.query.return_value.filter_by.return_value.first.side_effect = [
        existing_plan,
        existing_sub,
        None,
    ]
    test_user = MagicMock(id="user-uuid")

    with patch("plugins.subscription.subscription.models.TarifPlan"), patch(
        "plugins.subscription.subscription.models.Subscription"
    ), patch("vbwd.models.invoice.UserInvoice"), patch(
        "vbwd.models.invoice_line_item.InvoiceLineItem"
    ):
        demo_seed.seed_test_data(session, test_user)

    # plan + subscription existed → only the invoice + its line item are added
    assert session.add.call_count == 2


def test_seed_test_data_skips_invoice_when_one_already_exists():
    session = MagicMock()
    existing_plan = MagicMock()
    existing_sub = MagicMock(id="sub-uuid")
    existing_invoice = MagicMock()
    # plan → existing; subscription → existing; invoice → existing
    session.query.return_value.filter_by.return_value.first.side_effect = [
        existing_plan,
        existing_sub,
        existing_invoice,
    ]
    test_user = MagicMock(id="user-uuid")

    with patch("plugins.subscription.subscription.models.TarifPlan"), patch(
        "plugins.subscription.subscription.models.Subscription"
    ), patch("vbwd.models.invoice.UserInvoice"), patch(
        "vbwd.models.invoice_line_item.InvoiceLineItem"
    ):
        demo_seed.seed_test_data(session, test_user)

    # everything already existed → nothing added
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
