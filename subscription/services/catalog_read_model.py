"""Catalog read model — subscription-owned plan/category read projections.

Lets catalog consumers (e.g. ghrm, which declares ``dependencies=["subscription"]``)
read plan/category data without importing the subscription models. Consumed
directly by those plugins; core names no catalog vocabulary (S50.1).
"""
from typing import Dict, List
from uuid import UUID


class CatalogReadModel:
    """Read-only plan-catalog projections for catalog consumers."""

    def _session(self):
        from vbwd.extensions import db

        return db.session

    def category_labels_by_slugs(self, slugs: List[str]) -> Dict[str, str]:
        if not slugs:
            return {}
        from plugins.subscription.subscription.models import TarifPlanCategory

        rows = (
            self._session()
            .query(TarifPlanCategory)
            .filter(TarifPlanCategory.slug.in_(slugs))
            .all()
        )
        return {c.slug: c.name for c in rows}

    def plan_ids_in_category(self, category_slug: str) -> List[UUID]:
        from plugins.subscription.subscription.models import TarifPlanCategory

        category = (
            self._session()
            .query(TarifPlanCategory)
            .filter_by(slug=category_slug)
            .first()
        )
        if not category:
            return []
        return [plan.id for plan in category.tarif_plans]
