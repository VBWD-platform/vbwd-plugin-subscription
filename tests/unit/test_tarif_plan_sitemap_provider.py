"""S154 D10 — ``subscription-plans``: the cms sitemap v2 provider for tarif plans.

The paged sequence is the public list page ``/<plans_slug>`` followed by one
detail URL per active plan (``ORDER BY id``, paged in SQL). The switch
``seo_index_tarif_plans`` OFF → ``count()`` 0 and no rows. The provider is
cms-free: cms's ``SitemapEntry`` is injected, so these tests run without cms.
The repository is faked here; the SQL filter/paging is the integration test's.

Engineering requirements (binding, restated): TDD-first; DevOps-first;
SOLID/DI/DRY/Liskov; clean code; no overengineering; core untouched.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from plugins.subscription.subscription.sitemap_provider import (
    SITEMAP_PROVIDER_NAME,
    TarifPlanSitemapProvider,
)

UPDATED_AT = datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)


@dataclass
class FakeSitemapEntry:
    loc: str
    lastmod: Optional[str] = None


class InMemoryPlanRepository:
    """Honours the repository contract: count == length of the paged sequence."""

    def __init__(self, rows: List[Tuple[str, Optional[datetime]]]) -> None:
        self.rows = rows
        self.page_calls: List[Tuple[int, int]] = []

    def count_active(self) -> int:
        return len(self.rows)

    def list_active_sitemap_rows(self, offset: int, limit: int):
        self.page_calls.append((offset, limit))
        return self.rows[offset : offset + limit]


def _provider(rows=None, enabled=True, plans_path="/plans"):
    repository = InMemoryPlanRepository(
        rows if rows is not None else [("basic", UPDATED_AT), ("pro", None)]
    )
    provider = TarifPlanSitemapProvider(
        plan_repository_factory=lambda: repository,
        entry_factory=FakeSitemapEntry,
        is_indexing_enabled=lambda: enabled,
        plans_path=lambda: plans_path,
    )
    return provider, repository


def _locs(entries):
    return [entry.loc for entry in entries]


def test_the_provider_name_is_the_stable_public_chunk_name():
    provider, _ = _provider()

    assert SITEMAP_PROVIDER_NAME == "subscription-plans"
    assert provider.name == "subscription-plans"


def test_count_is_the_list_page_plus_every_active_plan():
    provider, _ = _provider()

    assert provider.count() == 3


def test_the_first_page_holds_the_list_then_the_plans():
    provider, _ = _provider()

    entries = provider.page(0, 50000)

    assert _locs(entries) == ["/plans", "/plans/basic", "/plans/pro"]
    assert entries[1].lastmod == UPDATED_AT.isoformat()
    assert entries[2].lastmod is None


def test_a_later_page_reads_the_plans_shifted_past_the_list_entry():
    provider, repository = _provider()

    entries = provider.page(2, 5)

    assert _locs(entries) == ["/plans/pro"]
    assert repository.page_calls == [(1, 5)]


def test_pages_concatenate_to_the_whole_sequence():
    provider, _ = _provider()

    pages = provider.page(0, 2) + provider.page(2, 2)

    assert _locs(pages) == ["/plans", "/plans/basic", "/plans/pro"]


def test_a_one_entry_first_page_reads_no_plan_rows():
    provider, repository = _provider()

    assert _locs(provider.page(0, 1)) == ["/plans"]
    assert repository.page_calls == []


def test_urls_follow_the_configured_base():
    provider, _ = _provider(plans_path="/pricing")

    assert _locs(provider.page(0, 2)) == ["/pricing", "/pricing/basic"]


def test_a_plan_slug_is_encoded_as_one_path_segment():
    provider, _ = _provider(rows=[("a b/c", None)])

    assert _locs(provider.page(1, 1)) == ["/plans/a%20b%2Fc"]


def test_switched_off_means_count_zero_and_no_rows():
    provider, repository = _provider(enabled=False)

    assert provider.count() == 0
    assert provider.page(0, 50000) == []
    assert repository.page_calls == []


def test_no_plans_still_lists_the_list_page():
    provider, _ = _provider(rows=[])

    assert provider.count() == 1
    assert _locs(provider.page(0, 10)) == ["/plans"]
