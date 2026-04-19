"""TarifPlan repository implementation."""
from typing import Optional, List
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
