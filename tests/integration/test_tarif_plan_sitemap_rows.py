"""S154 D10 — the SQL behind the ``subscription-plans`` sitemap provider.

``count_active`` and ``list_active_sitemap_rows`` agree on the public filter
(active plans only — the same rule as ``GET /api/v1/tarif-plans``), and the rows
page in a stable ``ORDER BY id`` window of ``(slug, updated_at)``. Plans are
created through the repository (no raw SQL); the shared test DB may already hold
plans, so assertions are relative to the rows this test creates.
"""
from uuid import UUID

import pytest

from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)
from vbwd.models.enums import BillingPeriod

PLAN_IDS = (
    UUID("00000000-0000-4000-8000-0000000d1001"),
    UUID("00000000-0000-4000-8000-0000000d1002"),
    UUID("00000000-0000-4000-8000-0000000d1003"),
)


def _save_plan(repository, plan_id, slug, is_active=True):
    return repository.save(
        TarifPlan(
            id=plan_id,
            name=slug,
            slug=slug,
            price=9.0,
            billing_period=BillingPeriod.MONTHLY,
            is_active=is_active,
        )
    )


def test_count_and_rows_cover_active_plans_only_in_id_order(db):
    repository = TarifPlanRepository(db.session)
    active_before = repository.count_active()

    _save_plan(repository, PLAN_IDS[2], "s154-d10-third")
    _save_plan(repository, PLAN_IDS[0], "s154-d10-first")
    _save_plan(repository, PLAN_IDS[1], "s154-d10-hidden", is_active=False)

    assert repository.count_active() == active_before + 2
    rows = repository.list_active_sitemap_rows(0, repository.count_active())
    created_slugs = [slug for slug, _ in rows if slug.startswith("s154-d10-")]
    assert created_slugs == ["s154-d10-first", "s154-d10-third"]
    assert all(updated_at is not None for _, updated_at in rows)


def test_rows_page_in_a_stable_window(db):
    repository = TarifPlanRepository(db.session)
    _save_plan(repository, PLAN_IDS[0], "s154-d10-first")
    _save_plan(repository, PLAN_IDS[2], "s154-d10-third")
    total = repository.count_active()

    whole = repository.list_active_sitemap_rows(0, total)
    paged = [
        row
        for offset in range(total)
        for row in repository.list_active_sitemap_rows(offset, 1)
    ]

    assert paged == whole


def test_cms_sitemap_lists_the_public_plan_pages(client, db):
    seo_registry = pytest.importorskip("plugins.cms.src.services.seo_registry")
    # Registered on enable whenever cms is installed (enabled or not).
    assert seo_registry.find_sitemap_provider("subscription-plans") is not None
    repository = TarifPlanRepository(db.session)
    _save_plan(repository, PLAN_IDS[0], "s154-d10-first")
    _save_plan(repository, PLAN_IDS[1], "s154-d10-hidden", is_active=False)

    index_response = client.get("/sitemap.xml")
    chunk_response = client.get("/sitemap-subscription-plans-1.xml")

    if index_response.status_code == 404:
        pytest.skip("cms is installed but not enabled: no sitemap routes")
    assert "sitemap-subscription-plans-1.xml" in index_response.get_data(as_text=True)
    assert chunk_response.status_code == 200
    chunk_body = chunk_response.get_data(as_text=True)
    assert "/plans</loc>" in chunk_body
    assert "/plans/s154-d10-first</loc>" in chunk_body
    assert "/plans/s154-d10-hidden<" not in chunk_body
