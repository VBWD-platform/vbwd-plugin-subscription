"""Subscription marketplace vendor-listings provider (integration).

The subscription vertical contributes a ``vendor_listings_provider`` the
marketplace registry calls to aggregate a user's plan listings. This test
exercises the provider DIRECTLY against a real session — it never imports the
marketplace plugin, so it passes in the per-plugin isolated CI (which clones
subscription alone).

Seeds through ``TarifPlanRepository`` (no raw SQL) and asserts:
  - an empty list for a vendor who owns nothing (Liskov: safe empty result),
  - the vendor's own ``TarifPlan.to_dict()`` for a vendor who owns one plan,
    enriched with ISO ``created_at`` / ``updated_at`` (the "Listings" tab's
    Created / Last updated columns) which ``to_dict`` itself omits,
  - another vendor's plan is excluded (ownership scoping).
"""
from uuid import uuid4

from vbwd.models.user import User

from plugins.subscription.subscription.marketplace_listings import (
    vendor_listings_provider,
)
from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)


def _make_vendor(db):
    """Seed a real core user (vendor_id has a FK to users) and return its id."""
    user = User(email=f"sub-vendor-{uuid4().hex}@example.com", password_hash="x")
    db.session.add(user)
    db.session.commit()
    return user.id


def _make_plan(db, vendor_id, name):
    repository = TarifPlanRepository(db.session)
    plan = TarifPlan(
        name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid4().hex[:8]}",
        description="Vendor plan",
        price=19.0,
        billing_period="MONTHLY",
        is_active=True,
        vendor_id=vendor_id,
    )
    return repository.save(plan)


def test_provider_returns_empty_for_vendor_without_plans(db):
    unknown_vendor_id = uuid4()

    assert vendor_listings_provider(unknown_vendor_id) == []


def test_provider_returns_only_the_vendors_own_plan_dicts(db):
    vendor_id = _make_vendor(db)
    other_vendor_id = _make_vendor(db)

    owned = _make_plan(db, vendor_id, "Owned Plan")
    _make_plan(db, other_vendor_id, "Other Plan")

    listings = vendor_listings_provider(vendor_id)

    assert len(listings) == 1
    # The provider enriches ``to_dict()`` with the timestamp columns, so the
    # dict is a superset of the plain serializer output.
    assert listings[0].items() >= owned.to_dict().items()
    assert listings[0]["id"] == str(owned.id)


def test_provider_dict_carries_iso_created_and_updated_timestamps(db):
    vendor_id = _make_vendor(db)
    owned = _make_plan(db, vendor_id, "Timestamped Plan")

    listings = vendor_listings_provider(vendor_id)

    assert len(listings) == 1
    listing = listings[0]
    assert listing["created_at"] == owned.created_at.isoformat()
    assert listing["updated_at"] == owned.updated_at.isoformat()
    assert listing["created_at"] is not None
    assert listing["updated_at"] is not None
