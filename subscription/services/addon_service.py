"""AddOn service — business logic for add-on management."""
from typing import Optional

from plugins.subscription.subscription.models import AddOn
from plugins.subscription.subscription.repositories.addon_repository import (
    AddOnRepository,
)
from plugins.subscription.subscription.services.copy_helpers import (
    COPY_NAME_SUFFIX,
    next_available_copy_slug,
    slugify,
)


class AddOnService:
    """Add-on management service (thin logic layer over the repository)."""

    def __init__(self, addon_repo: AddOnRepository):
        self._addon_repo = addon_repo

    def copy_addon(self, addon_id) -> Optional[AddOn]:
        """Duplicate an add-on, returning the persisted copy (or ``None``).

        The copy is always inactive, gets a fresh id/timestamps and a unique
        ``<base>-copy[-N]`` slug, and its name gains a ``(Copy)`` suffix. The
        M2M links (taxes, tariff-plan bindings) are RE-POINTED at the same rows
        — never duplicated. Add-ons own no children. Returns ``None`` when the
        source is gone so the caller can skip it in a bulk request.
        """
        source_addon = self._addon_repo.find_by_id(addon_id)
        if source_addon is None:
            return None

        base_slug = source_addon.slug or slugify(source_addon.name)
        new_addon = AddOn(
            name=f"{source_addon.name}{COPY_NAME_SUFFIX}",
            slug=next_available_copy_slug(
                base_slug,
                lambda candidate: self._addon_repo.slug_exists(candidate),
            ),
            description=source_addon.description,
            price=source_addon.price,
            billing_period=source_addon.billing_period,
            config=source_addon.config,
            sort_order=source_addon.sort_order,
            is_active=False,
        )
        # Re-point M2M links at the SAME rows (the source keeps its own links).
        new_addon.taxes = list(source_addon.taxes)
        new_addon.tarif_plans = list(source_addon.tarif_plans)

        return self._addon_repo.save(new_addon)
