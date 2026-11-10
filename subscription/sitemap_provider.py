"""Tarif plans as a cms sitemap provider (registry v2, S154 D10).

Implements the duck-typed cms contract — ``name``, ``count()``,
``page(offset, limit)`` — over the public plan pages: the list at
``/<plans_slug>`` first, then one ``/<plans_slug>/<plan_slug>`` per active plan
(the public ``GET /api/v1/tarif-plans`` rule), ``lastmod`` from ``updated_at``.
Paging happens in SQL (``COUNT`` + ``ORDER BY id OFFSET/LIMIT``); no full load.

This module never imports cms: the plugin wiring root injects cms's
``SitemapEntry`` class as ``entry_factory`` (cms is OPTIONAL, D3). The base slug
and the ``seo_index_tarif_plans`` switch are read on every call, so admin changes
apply without a restart; switched OFF means count 0 and empty pages.
"""
from typing import Any, Callable, List
from urllib.parse import quote

from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)
from plugins.subscription.subscription.services.plugin_config import (
    public_plans_path,
    tarif_plans_indexing_enabled,
)

# Part of the public chunk URL ``/sitemap-subscription-plans-<n>.xml`` — never rename.
SITEMAP_PROVIDER_NAME = "subscription-plans"
# The list page ``/<plans_slug>`` heads the sequence, before the plan rows.
LIST_PAGE_ENTRY_COUNT = 1


class TarifPlanSitemapProvider:
    """Sitemap entries for the public plan list and every active plan."""

    name = SITEMAP_PROVIDER_NAME

    def __init__(
        self,
        plan_repository_factory: Callable[[], TarifPlanRepository],
        entry_factory: Callable[..., Any],
        is_indexing_enabled: Callable[[], bool] = tarif_plans_indexing_enabled,
        plans_path: Callable[[], str] = public_plans_path,
    ) -> None:
        self._plan_repository_factory = plan_repository_factory
        self._entry_factory = entry_factory
        self._is_indexing_enabled = is_indexing_enabled
        self._plans_path = plans_path

    def count(self) -> int:
        if not self._is_indexing_enabled():
            return 0
        return LIST_PAGE_ENTRY_COUNT + self._plan_repository_factory().count_active()

    def page(self, offset: int, limit: int) -> List[Any]:
        if not self._is_indexing_enabled():
            return []
        base_path = self._plans_path()
        entries = []
        if offset < LIST_PAGE_ENTRY_COUNT:
            entries.append(self._entry_factory(loc=base_path))
        plan_limit = limit - len(entries)
        if plan_limit <= 0:
            return entries
        rows = self._plan_repository_factory().list_active_sitemap_rows(
            max(offset - LIST_PAGE_ENTRY_COUNT, 0), plan_limit
        )
        entries.extend(
            self._entry_factory(
                loc=f"{base_path}/{quote(slug, safe='')}",
                lastmod=updated_at.isoformat() if updated_at else None,
            )
            for slug, updated_at in rows
        )
        return entries
