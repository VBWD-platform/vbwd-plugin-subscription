"""Subscription catalogue cache keys + invalidation prefixes (S48.2).

The generic cache store lives in core (``vbwd.services.cache``) and knows
nothing about subscriptions. THIS module is the gnostic half: it owns the key
scheme and the prefixes the admin write paths clear, so the catalogue domain
vocabulary stays out of core.

Key scheme (all under their domain prefix so a single ``delete_prefix`` clears
every per-currency / per-country variant after an admin edit):

  plans  list   -> ``tarif-plans:list:{currency}:{country}:{category}``
  plans  detail -> ``tarif-plans:detail:{slug_or_id}:{currency}:{country}``
  addons list   -> ``addons:list:public``
"""
from typing import Optional

PLAN_CACHE_PREFIX = "tarif-plans:"
ADDON_CACHE_PREFIX = "addons:"

DEFAULT_CATALOG_TTL_SECONDS = 120


def catalog_cache_ttl_seconds() -> int:
    """TTL backstop for catalogue reads — read from app config (single source)."""
    try:
        from flask import current_app

        return int(
            current_app.config.get("CACHE_TTL_SECONDS", DEFAULT_CATALOG_TTL_SECONDS)
        )
    except Exception:
        return DEFAULT_CATALOG_TTL_SECONDS


def plan_list_cache_key(
    currency_code: str,
    country_code: Optional[str],
    category_slug: Optional[str],
) -> str:
    """Key for ``GET /api/v1/tarif-plans`` resolved per varying input."""
    return (
        f"{PLAN_CACHE_PREFIX}list:"
        f"{currency_code}:{country_code or ''}:{category_slug or ''}"
    )


def plan_detail_cache_key(
    slug_or_id: str,
    currency_code: str,
    country_code: Optional[str],
) -> str:
    """Key for ``GET /api/v1/tarif-plans/<slug_or_id>``."""
    return (
        f"{PLAN_CACHE_PREFIX}detail:"
        f"{slug_or_id}:{currency_code}:{country_code or ''}"
    )


def addon_list_cache_key() -> str:
    """Key for the PUBLIC ``GET /api/v1/addons/`` (independent add-ons only)."""
    return f"{ADDON_CACHE_PREFIX}list:public"


def invalidate_plan_cache() -> None:
    """Clear every cached plan list/detail entry (call after any admin write)."""
    from vbwd.services.cache import resolve_cache_store

    resolve_cache_store().delete_prefix(PLAN_CACHE_PREFIX)


def invalidate_addon_cache() -> None:
    """Clear the cached public add-on list (call after any admin add-on write)."""
    from vbwd.services.cache import resolve_cache_store

    resolve_cache_store().delete_prefix(ADDON_CACHE_PREFIX)
