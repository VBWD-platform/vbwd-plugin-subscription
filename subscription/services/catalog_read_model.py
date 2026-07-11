"""Catalog read model — subscription-owned plan/category read projections.

Lets catalog consumers (e.g. ghrm, which declares ``dependencies=["subscription"]``)
read plan/category data without importing the subscription models. Consumed
directly by those plugins; core names no catalog vocabulary (S50.1).
"""
import logging
from typing import Dict, List
from uuid import UUID

logger = logging.getLogger(__name__)


class CatalogReadModel:
    """Read-only plan-catalog projections for catalog consumers."""

    def _session(self):
        from vbwd.extensions import db

        return db.session

    def plan_prices_by_ids(self, plan_ids: List[UUID]) -> Dict[str, dict]:
        """Price blocks for many plans in ONE query, keyed by ``str(plan_id)``.

        Catalog consumers (ghrm) enrich their cards with the linked plan's price
        without importing subscription models or re-deriving the pricing block:
        the ``Price`` is computed by the core ``PriceFactory`` (resolved once)
        and serialised via the core ``build_pricing_block``, plus the plan's
        ``billing_period`` and its raw display price. Empty ids ⇒ ``{}``.

        A plan whose price cannot be resolved (missing double / a factory error
        on that one plan) is skipped — its id is simply absent, which the caller
        reads as "no price" (Liskov: one bad plan never kills the batch). Lazy
        imports and ``self._session()`` mirror the other read-model methods so
        core stays free of subscription vocabulary at import time.
        """
        if not plan_ids:
            return {}
        from flask import current_app

        from vbwd.pricing.price_payload import build_pricing_block
        from plugins.subscription.subscription.models import TarifPlan

        plans = (
            self._session().query(TarifPlan).filter(TarifPlan.id.in_(plan_ids)).all()
        )
        price_factory = current_app.container.price_factory()
        prices: Dict[str, dict] = {}
        for plan in plans:
            try:
                block = build_pricing_block(price_factory.get_price_from_object(plan))
            except Exception as error:
                logger.warning("Skipping price for plan %s: %s", plan.id, error)
                continue
            block["billing_period"] = (
                plan.billing_period.value if plan.billing_period else None
            )
            block["display_price"] = plan.raw_price
            prices[str(plan.id)] = block
        return prices

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
