"""Shared helpers for duplicating catalogue entities (plans / add-ons).

Both ``TarifPlanService.copy_plan`` and ``AddOnService.copy_addon`` need the
same collision-safe copy-slug scheme, so it lives here once (plugin-local, not
core) rather than being duplicated per service.
"""
import re
from typing import Callable

COPY_NAME_SUFFIX = " (Copy)"


def slugify(value: str) -> str:
    """Return a URL-safe slug for ``value`` (lowercase, hyphen-separated)."""
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def next_available_copy_slug(base_slug: str, slug_taken: Callable[[str], bool]) -> str:
    """Return the first free copy slug for ``base_slug``.

    Tries ``<base>-copy`` first, then ``<base>-copy-2``, ``<base>-copy-3`` ...
    until ``slug_taken`` reports the candidate is free. ``slug_taken`` is a
    predicate querying the owning repository, so bulk-copying the same source
    twice in one request (each copy committed before the next) never collides.
    """
    candidate = f"{base_slug}-copy"
    suffix = 2
    while slug_taken(candidate):
        candidate = f"{base_slug}-copy-{suffix}"
        suffix += 1
    return candidate
