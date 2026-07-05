"""End-of-subscription revoke of Features-declared access levels.

Two lifecycle paths must strip the access levels a plan declares in its
``features`` field (``access_levels: premium, vip``) once the subscription is no
longer active:

1. the USER cancel HTTP route (``POST /api/v1/user/subscriptions/<id>/cancel``)
   must publish ``subscription.cancelled`` so the wired
   ``SubscriptionAccessLevelHandler`` revokes the feature levels — previously the
   user route published nothing, so the levels persisted (Cause 1);
2. the scheduler expiry path (``subscription.expired``) must run the same revoke
   — previously the access-level handler was subscribed only to
   ``activated``/``cancelled``, so an expired subscription kept its levels
   (Cause 2). Overlap-safe: a level still declared by another active plan stays.

Both assertions go through the real global ``event_bus`` wiring set up by the
plugin's ``register_event_handlers`` in the ``app`` fixture, so they exercise the
actual subscribe list — not a hand-wired handler.
"""
from uuid import uuid4

from vbwd.models.enums import BillingPeriod, SubscriptionStatus
from vbwd.models.user import User
from vbwd.models.user_access_level import AccessLevel
from vbwd.services.user_access_level_service import UserAccessLevelService

from plugins.subscription.subscription.models import Subscription, TarifPlan
from plugins.subscription.subscription.services.lifecycle_events import (
    EVENT_SUBSCRIPTION_EXPIRED,
    publish_subscription_event,
)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(app, email):
    from vbwd.extensions import db
    from vbwd.repositories.user_repository import UserRepository

    user_repository = UserRepository(db.session)
    auth_service = app.container.auth_service()
    if user_repository.find_by_email(email) is None:
        auth_service.register(email=email, password="Access123@")
        db.session.commit()
    user = user_repository.find_by_email(email)
    login = auth_service.login(email=email, password="Access123@")
    return user, login.token


def _access_level(db, slug: str) -> AccessLevel:
    level = AccessLevel(id=uuid4(), name=f"Level {slug}", slug=slug)
    db.session.add(level)
    db.session.flush()
    return level


def _plan(db, features: dict) -> TarifPlan:
    plan = TarifPlan(
        id=uuid4(),
        name=f"plan-{uuid4().hex}",
        slug=f"plan-{uuid4().hex}",
        description="plan",
        price=9.99,
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
        sort_order=0,
        features=features,
    )
    db.session.add(plan)
    db.session.flush()
    return plan


def _subscribe(db, user_id, plan_id, status=SubscriptionStatus.ACTIVE):
    subscription = Subscription(user_id=user_id, tarif_plan_id=plan_id, status=status)
    db.session.add(subscription)
    db.session.flush()
    return subscription


def _user_level_slugs(db, user_id):
    refreshed = db.session.get(User, user_id)
    return {level.slug for level in refreshed.assigned_user_access_levels}


def test_user_cancel_route_revokes_feature_access_level(app, db, client):
    """Cause 1: the user cancel route must publish so the level is revoked."""
    premium = _access_level(db, "premium")
    user, token = _register(app, f"cancel-access-{uuid4().hex[:6]}@example.com")
    plan = _plan(db, {"access_levels": "premium"})
    subscription = _subscribe(db, user.id, plan.id)

    # Grant-on-activate already ran (user holds the feature level).
    UserAccessLevelService(db.session).assign(user.id, premium.id)
    db.session.commit()
    assert "premium" in _user_level_slugs(db, user.id)

    response = client.post(
        f"/api/v1/user/subscriptions/{subscription.id}/cancel", headers=_auth(token)
    )
    assert response.status_code == 200, response.get_json()

    assert "premium" not in _user_level_slugs(db, user.id)


def test_expired_event_revokes_feature_access_level_overlap_safe(app, db):
    """Cause 2: the expiry lifecycle fact must run the same revoke, overlap-safe."""
    premium = _access_level(db, "premium")
    vip = _access_level(db, "vip")
    user = User(email=f"expire-access-{uuid4().hex}@example.com", password_hash="x")
    db.session.add(user)
    db.session.flush()

    plan_a = _plan(db, {"access_levels": "premium, vip"})
    plan_b = _plan(db, {"access_levels": "vip"})
    subscription_a = _subscribe(db, user.id, plan_a.id, SubscriptionStatus.EXPIRED)
    _subscribe(db, user.id, plan_b.id, SubscriptionStatus.ACTIVE)

    service = UserAccessLevelService(db.session)
    service.assign(user.id, premium.id)
    service.assign(user.id, vip.id)
    db.session.commit()
    assert _user_level_slugs(db, user.id) == {"premium", "vip"}

    # Scheduler expiry publishes ``subscription.expired`` for plan A; the wired
    # access-level handler must revoke 'premium' (exclusive to the expired plan)
    # while retaining 'vip' (still declared by active plan B).
    publish_subscription_event(EVENT_SUBSCRIPTION_EXPIRED, subscription_a, user.id)
    db.session.commit()

    assert _user_level_slugs(db, user.id) == {"vip"}
