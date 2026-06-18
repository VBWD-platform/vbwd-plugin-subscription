"""Subscription plan search provider (cross-entity search seam).

Contributes ACTIVE tariff plans to the core ``search_provider_registry`` so the
``/search`` bot can find them. Searches only public, non-personal fields
(name / description / slug) over active plans and re-resolves a tapped hit by
slug. The public fe-user detail route is ``/dashboard/plan/<slug>``.
"""
from __future__ import annotations

from typing import List, Optional

from vbwd.services.search import SearchHit

ENTITY_TYPE = "subscription_plan"
ENTITY_LABEL = "Plans"
DETAIL_URL_TEMPLATE = "/dashboard/plan/{slug}"


class SubscriptionPlanSearchProvider:
    """A ``SearchProvider`` for active subscription plans."""

    entity_type: str = ENTITY_TYPE
    entity_label: str = ENTITY_LABEL

    def search(self, query: str, *, limit: int = 5) -> List[SearchHit]:
        if not query or not query.strip():
            return []
        from sqlalchemy import or_
        from vbwd.extensions import db
        from plugins.subscription.subscription.models import TarifPlan

        pattern = f"%{query.strip()}%"
        plans = (
            db.session.query(TarifPlan)
            .filter(
                TarifPlan.is_active.is_(True),
                or_(
                    TarifPlan.name.ilike(pattern),
                    TarifPlan.description.ilike(pattern),
                    TarifPlan.slug.ilike(pattern),
                ),
            )
            .order_by(TarifPlan.sort_order, TarifPlan.name)
            .limit(limit)
            .all()
        )
        return [self._to_hit(plan) for plan in plans]

    def get_detail(self, key: str) -> Optional[SearchHit]:
        from vbwd.extensions import db
        from plugins.subscription.subscription.repositories.tarif_plan_repository import (  # noqa: E501
            TarifPlanRepository,
        )

        repository = TarifPlanRepository(db.session)
        plan = repository.find_by_slug(key)
        if plan is None:
            plan = self._find_by_id(repository, key)
        # Only surface ACTIVE plans (an inactive plan is not a live result).
        if plan is None or not plan.is_active:
            return None
        return self._to_hit(plan)

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _find_by_id(repository, key: str):
        try:
            return repository.find_by_id(key)
        except Exception:  # noqa: BLE001 — a non-uuid key is simply "not found"
            return None

    def _to_hit(self, plan) -> SearchHit:
        return SearchHit(
            entity_type=self.entity_type,
            entity_label=self.entity_label,
            key=plan.slug,
            title=plan.name,
            snippet=self._snippet(plan.description),
            url=DETAIL_URL_TEMPLATE.format(slug=plan.slug),
            price=_format_price(plan.price),
        )

    @staticmethod
    def _snippet(description: Optional[str], *, max_length: int = 160) -> str:
        if not description:
            return ""
        text = description.strip()
        if len(text) <= max_length:
            return text
        return text[: max_length - 1].rstrip() + "…"


def _format_price(amount: Optional[float]) -> Optional[str]:
    """A best-effort display string ``"<amount> <currency>"`` (no client math)."""
    if amount is None:
        return None
    from vbwd.services.core_settings_store import get_default_currency

    # Reads the operating currency (file-backed; degrades to the schema default
    # on its own — never a call-site literal, never raises).
    currency = get_default_currency()
    return f"{float(amount):.2f} {currency}"
