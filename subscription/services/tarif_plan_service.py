"""TarifPlan service implementation."""
from typing import Optional, List
from uuid import UUID
from decimal import Decimal
from plugins.subscription.subscription.repositories.tarif_plan_repository import TarifPlanRepository
from plugins.subscription.subscription.models import TarifPlan


class TarifPlanService:
    """
    Tariff plan management service.

    Handles plan retrieval and pricing calculations.
    """

    def __init__(
        self,
        tarif_plan_repo: TarifPlanRepository,
        currency_service=None,
        tax_service=None,
    ):
        """Initialize TarifPlanService.

        Args:
            tarif_plan_repo: Repository for tariff plan data access
            currency_service: Optional currency service for conversions
            tax_service: Optional tax service for tax calculations
        """
        self._tarif_plan_repo = tarif_plan_repo
        self._currency_service = currency_service
        self._tax_service = tax_service

    def get_active_plans(self) -> List[TarifPlan]:
        """Get all active tariff plans.

        Returns:
            List of active tariff plans
        """
        return self._tarif_plan_repo.find_active()

    def get_plan_by_slug(self, slug: str) -> Optional[TarifPlan]:
        """Get tariff plan by slug.

        Args:
            slug: Plan URL slug

        Returns:
            TarifPlan if found, None otherwise
        """
        return self._tarif_plan_repo.find_by_slug(slug)

    def get_plan_by_id(self, plan_id: UUID) -> Optional[TarifPlan]:
        """Get tariff plan by ID.

        Args:
            plan_id: Plan UUID

        Returns:
            TarifPlan if found, None otherwise
        """
        return self._tarif_plan_repo.find_by_id(plan_id)

    def get_plan_with_pricing(
        self,
        plan: TarifPlan,
        currency_code: str,
        country_code: Optional[str] = None,
    ) -> dict:
        """Get plan with pricing details in specified currency.

        Args:
            plan: TarifPlan object
            currency_code: ISO currency code for display
            country_code: Optional ISO country code for tax calculation

        Returns:
            Dictionary with plan details and pricing information
        """
        if not self._currency_service:
            raise ValueError("Currency service required for pricing calculations")

        currency = self._currency_service.get_currency_by_code(currency_code)
        if not currency:
            raise ValueError(f"Unknown currency: {currency_code}")

        result = {
            "id": str(plan.id),
            "name": plan.name,
            "slug": plan.slug,
            "display_currency": currency.code,
            "display_price": plan.price_float,
        }

        # Add tax breakdown if country code provided
        if country_code and self._tax_service:
            tax_breakdown = self._tax_service.get_tax_breakdown(
                Decimal(str(plan.price_float)), country_code
            )
            result.update(
                {
                    "net_price": tax_breakdown["net_amount"],
                    "tax_amount": tax_breakdown["tax_amount"],
                    "gross_price": tax_breakdown["gross_amount"],
                    "tax_rate": tax_breakdown["tax_rate"],
                }
            )

        return result
