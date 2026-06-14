"""TarifPlan service implementation."""
from typing import Optional, List
from uuid import UUID
from decimal import Decimal
from vbwd.pricing.display_mode import display_mode_fields
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)
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
        price_factory=None,
    ):
        """Initialize TarifPlanService.

        Args:
            tarif_plan_repo: Repository for tariff plan data access
            currency_service: Optional currency service for conversions
            tax_service: Optional tax service for tax calculations
            price_factory: The core ``PriceFactory`` (D1). When provided, the
                assigned-tax breakdown is computed by the factory (mode-aware)
                and the serialized ``price`` object is attached. When absent
                (legacy callers), the inline NETTO breakdown is used.
        """
        self._tarif_plan_repo = tarif_plan_repo
        self._currency_service = currency_service
        self._tax_service = tax_service
        self._price_factory = price_factory

    def _breakdown_from_assigned_taxes(self, plan: TarifPlan) -> Optional[dict]:
        """Resolve the net/tax/gross breakdown for the plan's *assigned* taxes.

        Returns ``None`` when the plan carries no assigned taxes, so the caller
        falls back to the country-based breakdown. S85.2 (D1): when a
        ``PriceFactory`` is wired, the breakdown is computed by the factory
        (honouring the global ``prices_mode_in_db``) and the serialized
        ``price`` object is included; otherwise the inline NETTO breakdown runs.
        """
        taxes = getattr(plan, "taxes", None) or []
        if not taxes:
            return None

        if self._price_factory is not None:
            return self._factory_breakdown(plan, taxes)

        net_amount = Decimal(str(plan.raw_price))
        tax_amount = Decimal("0.00")
        rate_total = Decimal("0.00")
        for tax in taxes:
            tax_amount += tax.calculate(net_amount)
            rate_total += tax.rate

        return {
            "net_amount": net_amount,
            "tax_amount": tax_amount,
            "gross_amount": net_amount + tax_amount,
            "tax_rate": rate_total,
            "taxes": [
                {
                    "id": str(tax.id),
                    "code": tax.code,
                    "name": tax.name,
                    "rate": str(tax.rate),
                }
                for tax in taxes
            ],
        }

    def _factory_breakdown(self, plan: TarifPlan, taxes) -> dict:
        """Compute the net/tax/gross breakdown via the core ``PriceFactory``.

        Money values stay ``Decimal`` (the catalog payload's established type);
        the rate sum mirrors the inline path. The serialized ``price`` object is
        attached so the unified consumer can read the computed VO directly.
        """
        price = self._price_factory.get_price_from_object(plan)
        rate_total = sum((tax.rate for tax in taxes), Decimal("0.00"))
        total_tax = sum((Decimal(str(t.amount)) for t in price.taxes), Decimal("0"))
        return {
            "net_amount": Decimal(str(price.netto)),
            "tax_amount": total_tax,
            "gross_amount": Decimal(str(price.brutto)),
            "tax_rate": rate_total,
            "taxes": [
                {
                    "id": str(tax.id),
                    "code": tax.code,
                    "name": tax.name,
                    "rate": str(tax.rate),
                }
                for tax in taxes
            ],
            "price": price.to_dict(),
        }

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
            "display_price": plan.raw_price,
        }
        # S72.4: both pricing paths carry the global mode + per-plan effective
        # mode so the fe-user consumer can pick net/gross + the "netto price" tag.
        result.update(display_mode_fields(plan))

        # Assigned taxes take precedence over the country-based breakdown.
        applied_breakdown = self._breakdown_from_assigned_taxes(plan)
        if applied_breakdown is not None:
            result.update(applied_breakdown)
            return result

        # Add tax breakdown if country code provided
        if country_code and self._tax_service:
            tax_breakdown = self._tax_service.get_tax_breakdown(
                Decimal(str(plan.raw_price)), country_code
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
