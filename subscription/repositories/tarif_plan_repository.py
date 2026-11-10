"""TarifPlan repository implementation."""
from datetime import datetime
from typing import Optional, List, Tuple
from vbwd.repositories.base import BaseRepository
from plugins.subscription.subscription.models import TarifPlan


class TarifPlanRepository(BaseRepository[TarifPlan]):
    """Repository for TarifPlan entity operations."""

    def __init__(self, session):
        super().__init__(session=session, model=TarifPlan)

    def find_by_slug(self, slug: str) -> Optional[TarifPlan]:
        """Find tariff plan by slug."""
        return self._session.query(TarifPlan).filter(TarifPlan.slug == slug).first()

    def find_active(self) -> List[TarifPlan]:
        """Find all active tariff plans."""
        return (
            self._session.query(TarifPlan)
            .filter(TarifPlan.is_active.is_(True))
            .order_by(TarifPlan.sort_order)
            .all()
        )

    def count_active(self) -> int:
        """How many plans are public (active) — the sitemap ``count()``."""
        return (
            self._session.query(TarifPlan).filter(TarifPlan.is_active.is_(True)).count()
        )

    def list_active_sitemap_rows(
        self, offset: int, limit: int
    ) -> List[Tuple[str, Optional[datetime]]]:
        """``(slug, updated_at)`` of active plans, one stable ``id``-ordered page.

        Selects the two columns only (no ``TarifPlan`` entities, no ``selectin``
        tax loads). Same filter as ``count_active``.
        """
        return [
            (slug, updated_at)
            for slug, updated_at in self._session.query(
                TarifPlan.slug, TarifPlan.updated_at
            )
            .filter(TarifPlan.is_active.is_(True))
            .order_by(TarifPlan.id)
            .offset(offset)
            .limit(limit)
        ]

    def find_by_vendor(self, vendor_id) -> List[TarifPlan]:
        """Find all tariff plans owned by a given vendor (vendor-mode)."""
        return (
            self._session.query(TarifPlan)
            .filter(TarifPlan.vendor_id == vendor_id)
            .order_by(TarifPlan.sort_order)
            .all()
        )

    def find_recurring(self) -> List[TarifPlan]:
        """Find all recurring tariff plans."""
        from vbwd.models.enums import BillingPeriod

        return (
            self._session.query(TarifPlan)
            .filter(
                TarifPlan.is_active.is_(True),
                TarifPlan.billing_period != BillingPeriod.ONE_TIME,
            )
            .order_by(TarifPlan.sort_order)
            .all()
        )
