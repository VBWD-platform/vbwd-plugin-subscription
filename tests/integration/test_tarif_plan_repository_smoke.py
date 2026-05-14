"""Smoke integration test: prove the subscription TarifPlanRepository round-trips
through a real PostgreSQL session.

Goes through TarifPlanRepository (no raw SQL — see
feedback_no_direct_db_for_test_data.md) and asserts the row is fetchable
both by slug and through the active-listing filter.

Sized to be the cheapest assertion that defends:
  - the SQLAlchemy mapper for TarifPlan
  - the subscription conftest wiring (test DB, create_all, drop_all)
  - BaseRepository.save() committing rather than just flushing

Sprint: docs/dev_log/20260514/sprints/03-subscription-ci-fix.md
"""
from uuid import uuid4

from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)
from vbwd.models.enums import BillingPeriod


def test_tarif_plan_save_then_round_trips_through_real_db(db):
    repository = TarifPlanRepository(db.session)

    new_plan = TarifPlan(
        id=uuid4(),
        name="Smoke Plan",
        slug="smoke-plan",
        description="Round-trip smoke test plan",
        price_float=19.99,
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
        sort_order=0,
    )
    saved_plan = repository.save(new_plan)

    fetched_by_slug = repository.find_by_slug("smoke-plan")
    active_plans = repository.find_active()

    assert fetched_by_slug is not None
    assert fetched_by_slug.id == saved_plan.id
    assert fetched_by_slug.price_float == 19.99
    assert fetched_by_slug.billing_period == BillingPeriod.MONTHLY

    assert any(plan.id == saved_plan.id for plan in active_plans)
