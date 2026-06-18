"""SubscriptionPlanSearchProvider — search hits + get_detail over real rows.

A query finds ACTIVE plans by name/description/slug; the hit carries the public
``/dashboard/plan/<slug>`` url + a price string; ``get_detail`` re-resolves by
slug and never surfaces an INACTIVE plan.
"""
from decimal import Decimal
from uuid import uuid4

import pytest

from vbwd.models.enums import BillingPeriod
from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from plugins.subscription.subscription.search_provider import (
    SubscriptionPlanSearchProvider,
)


def _make_plan(db, *, name, slug, description="", price="29.99", is_active=True):
    plan = TarifPlan(
        id=uuid4(),
        name=name,
        slug=slug,
        description=description,
        price=Decimal(price),
        billing_period=BillingPeriod.MONTHLY,
        is_active=is_active,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


@pytest.fixture
def provider():
    return SubscriptionPlanSearchProvider()


def test_search_finds_active_plan_by_name(db, provider):
    _make_plan(
        db,
        name="Professional",
        slug="professional",
        description="For power users.",
        price="29.99",
    )

    hits = provider.search("profession", limit=5)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.entity_type == "subscription_plan"
    assert hit.entity_label == "Plans"
    assert hit.key == "professional"
    assert hit.title == "Professional"
    assert hit.url == "/dashboard/plan/professional"
    assert hit.price is not None and "29.99" in hit.price


def test_search_excludes_inactive_plan(db, provider):
    _make_plan(db, name="Legacy", slug="legacy", is_active=False)

    assert provider.search("legacy", limit=5) == []


def test_get_detail_resolves_active_plan_by_slug(db, provider):
    _make_plan(db, name="Starter", slug="starter", description="Get going.")

    hit = provider.get_detail("starter")

    assert hit is not None
    assert hit.title == "Starter"
    assert hit.url == "/dashboard/plan/starter"


def test_get_detail_inactive_plan_returns_none(db, provider):
    _make_plan(db, name="Retired", slug="retired", is_active=False)

    assert provider.get_detail("retired") is None


def test_get_detail_unknown_slug_returns_none(db, provider):
    assert provider.get_detail("ghost") is None
