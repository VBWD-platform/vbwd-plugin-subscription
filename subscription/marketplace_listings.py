"""Marketplace vendor-listings provider for the subscription vertical.

The central ``marketplace`` plugin owns a registry that aggregates a user's
listings across every enabled vertical (its admin "what does this user sell?"
view). Subscription contributes a provider that returns the raw ``TarifPlan``
dicts a given vendor owns — mirroring the ``vendor_list_plans`` GET route.

This module never imports the marketplace plugin (the money path stays
decoupled — see ``test_vendor_mode_contract``); the actual registration onto the
marketplace registry is a guarded, soft import done in the plugin's ``on_enable``
(``plugins/subscription/__init__.py``), so the per-plugin isolated CI
(subscription without marketplace) still enables cleanly. Core names nothing.
"""
from typing import List
from uuid import UUID

# The listing ``type`` id subscription contributes — mirrors the marketplace
# ``LISTING_TYPE_CATALOG`` and the fe-user ``ListingType`` for plans.
SUBSCRIPTION_LISTING_TYPE_ID = "plan"


def vendor_listings_provider(user_id: UUID) -> List[dict]:
    """Return the raw ``TarifPlan`` dicts owned by ``user_id`` (the vendor).

    Resolves ``db.session`` and constructs the repository lazily at call time
    (the call happens inside a Flask request), so there is no app-context work
    at import time. Reuses exactly what ``vendor_list_plans`` reads.
    """
    from vbwd.extensions import db
    from plugins.subscription.subscription.repositories.tarif_plan_repository import (
        TarifPlanRepository,
    )

    plans = TarifPlanRepository(db.session).find_by_vendor(user_id)
    return [
        {
            **plan.to_dict(),
            # ``TarifPlan.to_dict`` omits these BaseModel timestamps; the
            # marketplace "Listings" tab needs Created / Last updated columns,
            # so surface them here (null-safe) without touching the shared
            # serializer.
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
            "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
        }
        for plan in plans
    ]
