"""TarifPlanCategory repository implementation."""
from typing import Optional, List
from uuid import UUID
from vbwd.repositories.base import BaseRepository
from plugins.subscription.subscription.models import TarifPlanCategory


class TarifPlanCategoryRepository(BaseRepository[TarifPlanCategory]):
    """Repository for TarifPlanCategory entity operations."""

    def __init__(self, session):
        super().__init__(session=session, model=TarifPlanCategory)

    def find_by_slug(self, slug: str) -> Optional[TarifPlanCategory]:
        """Find category by slug."""
        return (
            self._session.query(TarifPlanCategory)
            .filter(TarifPlanCategory.slug == slug)
            .first()
        )

    def find_root_categories(self) -> List[TarifPlanCategory]:
        """Find all root categories (no parent)."""
        return (
            self._session.query(TarifPlanCategory)
            .filter(TarifPlanCategory.parent_id.is_(None))
            .order_by(TarifPlanCategory.sort_order)
            .all()
        )

    def find_children(self, parent_id: UUID) -> List[TarifPlanCategory]:
        """Find direct children of a category."""
        return (
            self._session.query(TarifPlanCategory)
            .filter(TarifPlanCategory.parent_id == parent_id)
            .order_by(TarifPlanCategory.sort_order)
            .all()
        )

    def find_by_plan_id(self, plan_id: UUID) -> List[TarifPlanCategory]:
        """Find all categories that contain a given plan."""
        return (
            self._session.query(TarifPlanCategory)
            .filter(TarifPlanCategory.tarif_plans.any(id=plan_id))
            .order_by(TarifPlanCategory.sort_order)
            .all()
        )
