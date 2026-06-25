"""S103.2b — ``find_expired_trials(now=None)`` accepts an injected clock.

The query selects TRIALING subscriptions whose ``trial_end_at <= now``. Passing
``now`` makes the window deterministic in tests and lets the conversion service
share a single clock with the repository.
"""
from datetime import timedelta
from uuid import uuid4

from vbwd.models.enums import BillingPeriod, SubscriptionStatus
from vbwd.models.user import User
from vbwd.utils.datetime_utils import utcnow

from plugins.subscription.subscription.models import Subscription, TarifPlan
from plugins.subscription.subscription.repositories.subscription_repository import (
    SubscriptionRepository,
)


def _make_trial(db, trial_end_at):
    user = User(email=f"trial-{uuid4().hex}@example.com", password_hash="x")
    plan = TarifPlan(
        name="Trial Plan",
        slug=f"trial-plan-{uuid4().hex}",
        price=10.0,
        is_active=True,
        billing_period=BillingPeriod.MONTHLY,
    )
    db.session.add_all([user, plan])
    db.session.flush()
    subscription = Subscription(
        user_id=user.id,
        tarif_plan_id=plan.id,
        status=SubscriptionStatus.TRIALING,
        trial_end_at=trial_end_at,
    )
    db.session.add(subscription)
    db.session.flush()
    return subscription


def test_find_expired_trials_respects_injected_now(db):
    now = utcnow()
    ended = _make_trial(db, trial_end_at=now - timedelta(hours=1))
    future = _make_trial(db, trial_end_at=now + timedelta(days=5))
    db.session.commit()

    repo = SubscriptionRepository(db.session)
    found = repo.find_expired_trials(now=now)
    found_ids = {str(s.id) for s in found}

    assert str(ended.id) in found_ids
    assert str(future.id) not in found_ids


def test_find_expired_trials_defaults_to_utcnow(db):
    past = _make_trial(db, trial_end_at=utcnow() - timedelta(days=1))
    db.session.commit()

    repo = SubscriptionRepository(db.session)
    found_ids = {str(s.id) for s in repo.find_expired_trials()}
    assert str(past.id) in found_ids
