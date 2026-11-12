"""IndexNow ping for tarif plans (S154 D4 / D10) — cms stays optional.

Calls cms's stable ``ping_indexnow_urls`` seam through a lazy import (a silent
no-op when cms is absent or not enabled), mirroring the optional-dependency
pattern the ``TarifPlanSitemapProvider`` already uses for the sitemap registry
(D3). The public paths are built from the SAME ``public_plans_path`` helper the
sitemap provider uses (DRY — never recomputed), gated by the plan type's own
fe-admin "indexable" switch (D7) — the identical switch the sitemap provider
checks.

A plan change also pings the public plan LIST page (``/<plans_slug>``), since
the list's content (which plans appear) changes right along with the plan row
— exactly what the sitemap provider's list entry represents.
"""
import logging
from typing import List

from plugins.subscription.subscription.services.plugin_config import (
    public_plans_path,
    tarif_plans_indexing_enabled,
)

logger = logging.getLogger(__name__)


def _import_ping_indexnow_urls():
    """cms's stable IndexNow ping seam (raises ``ImportError`` without cms)."""
    from plugins.cms.src.services.seo_wiring import ping_indexnow_urls

    return ping_indexnow_urls


def _ping_many(paths: List[str]) -> None:
    """Best-effort: a missing cms or a failing ping never breaks the caller."""
    try:
        ping_indexnow_urls = _import_ping_indexnow_urls()
    except ImportError:
        return
    try:
        ping_indexnow_urls(paths)
    except Exception as exc:  # pragma: no cover - defensive, best-effort
        logger.warning("[subscription.indexnow] ping failed for %s: %s", paths, exc)


def notify_plan_changed(slug: str) -> None:
    """Ping IndexNow of a changed plan's detail + list URL (switch-gated, D7)."""
    if not slug or not tarif_plans_indexing_enabled():
        return
    base_path = public_plans_path()
    _ping_many([f"{base_path}/{slug}", base_path])


def notify_plan_renamed(previous_slug: str, new_slug: str) -> None:
    """Ping IndexNow for a plan slug rename (switch-gated, D7).

    Both the OLD detail URL (so the search engine discovers it is gone/redirected)
    and the NEW one, plus the list page, in ONE POST (D4 batching) — mirrors cms's
    own permalink-rename pattern.
    """
    if not tarif_plans_indexing_enabled():
        return
    base_path = public_plans_path()
    detail_paths = [f"{base_path}/{slug}" for slug in (previous_slug, new_slug) if slug]
    if not detail_paths:
        return
    _ping_many(detail_paths + [base_path])
