"""Integration regression (S48.5): ``GET /api/v1/tarif-plans/<slug>`` must
return **200 with pricing** for every plan the list endpoint returns, on the
same seeded data the load test uses.

Measured bug: under load the *detail* route failed 100% with ``400 BAD
REQUEST`` while the *list* route succeeded. Root cause: no baseline ``EUR``
currency is seeded, so ``CurrencyService.get_currency_by_code("EUR")`` returns
``None`` → ``TarifPlanService.get_plan_with_pricing`` raises ``ValueError`` →
the detail handler mapped it to **400** (the list handler degrades gracefully
to 200 with the base price).

These tests pin the fix at two layers:
  - the detail route degrades gracefully (200 base price) when the requested
    currency is absent, exactly like the list route — so a missing FX rate
    never 400s;
  - ``populate_db.populate()`` seeds the baseline ``EUR`` currency through the
    repository (no raw SQL), so the seeded DB resolves priced bodies.
"""
from decimal import Decimal
from uuid import uuid4

from vbwd.models.enums import BillingPeriod
from plugins.subscription.subscription.models.tarif_plan import TarifPlan


def _make_plan(db, slug="detail-pricing-plan", price="29.99"):
    plan = TarifPlan(
        id=uuid4(),
        name="Detail Pricing Plan",
        slug=slug,
        description="Regression plan for the detail-route 400 bug",
        price=Decimal(price),
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
        sort_order=0,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def test_detail_route_returns_200_with_base_price(db, client):
    """The exact load-test path: a real slug, no query params → must NOT 400.

    The detail route always returns 200 carrying the base ``price`` (the
    regression this pins: it never 400s the way the original bug did).
    """
    plan = _make_plan(db)

    response = client.get(f"/api/v1/tarif-plans/{plan.slug}")

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["slug"] == plan.slug
    assert body["price"] == 29.99


def test_detail_route_returns_200_with_eur_seeded(db, client):
    """With the baseline EUR currency present, the detail route returns a
    priced body.

    The baseline EUR row is seeded by the integration ``db`` fixture (S85.2:
    the ``PriceFactory`` resolves the default currency from the catalog), so
    this test does not re-seed it.
    """
    plan = _make_plan(db)

    response = client.get(f"/api/v1/tarif-plans/{plan.slug}")

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["display_currency"] == "EUR"
    assert body["display_price"] == 29.99


def test_detail_route_returns_200_for_unknown_currency_param(db, client):
    """A currency the DB does not know (?currency=USD with only EUR seeded)
    must degrade gracefully to 200, never 400.

    Only EUR is present (seeded by the integration ``db`` fixture).
    """
    plan = _make_plan(db)

    response = client.get(f"/api/v1/tarif-plans/{plan.slug}?currency=USD")

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["slug"] == plan.slug


def test_every_listed_slug_returns_200_from_detail(db, client):
    """List then fetch each slug → all 200. Mirrors the Locust scenario."""
    _make_plan(db, slug="plan-alpha", price="9.99")
    _make_plan(db, slug="plan-beta", price="19.99")
    _make_plan(db, slug="plan-gamma", price="99.99")

    listed = client.get("/api/v1/tarif-plans")
    assert listed.status_code == 200
    slugs = [plan["slug"] for plan in listed.get_json()["plans"]]
    assert {"plan-alpha", "plan-beta", "plan-gamma"} <= set(slugs)

    for slug in slugs:
        detail = client.get(f"/api/v1/tarif-plans/{slug}")
        assert detail.status_code == 200, (slug, detail.get_json())


def test_populate_db_seeds_baseline_eur_currency(db):
    """``flask seed all`` (subscription populate_db) keeps the baseline EUR
    currency present through the repository so the seeded DB resolves pricing.

    Idempotent: with the baseline EUR already present (seeded by the
    integration ``db`` fixture, S85.2), ``seed_baseline_currency`` is a no-op
    and reports ``False`` on every call while EUR stays resolvable.
    """
    from plugins.subscription.populate_db import seed_baseline_currency
    from vbwd.repositories.currency_repository import CurrencyRepository

    assert seed_baseline_currency() is False
    assert seed_baseline_currency() is False

    eur = CurrencyRepository(db.session).find_by_code("EUR")
    assert eur is not None
    # S84: the default lives in the core settings JSON, not on a column.
    from vbwd.services.core_settings_store import get_core_settings

    assert get_core_settings()["default_currency"] == "EUR"
