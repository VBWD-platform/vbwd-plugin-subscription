"""S103.2a — the checkout-created subscription carries the selected method code.

The user's chosen payment method (``event.payment_method_code``) must be stored
on the Subscription so trial-end conversion can re-charge the same saved method
off-session.
"""
from uuid import uuid4

from vbwd.models.enums import BillingPeriod
from vbwd.models.user import User

from plugins.subscription.subscription.events import CheckoutRequestedEvent
from plugins.subscription.subscription.handlers.checkout_handler import CheckoutHandler
from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.repositories.subscription_repository import (
    SubscriptionRepository,
)


def test_checkout_persists_selected_payment_method_on_subscription(db, app):
    user = User(email=f"checkout-{uuid4().hex}@example.com", password_hash="x")
    plan = TarifPlan(
        name="Plan A",
        slug=f"plan-a-{uuid4().hex}",
        price=10.0,
        is_active=True,
        billing_period=BillingPeriod.MONTHLY,
    )
    db.session.add_all([user, plan])
    db.session.commit()

    handler = CheckoutHandler(app.container)
    event = CheckoutRequestedEvent(
        user_id=user.id,
        plan_id=plan.id,
        currency="EUR",
        payment_method_code="token_balance",
    )
    result = handler.handle(event)
    db.session.commit()

    assert result.success, result.error
    subscription_repo = SubscriptionRepository(db.session)
    subscriptions = subscription_repo.find_by_user(user.id)
    assert len(subscriptions) == 1
    assert subscriptions[0].payment_method == "token_balance"
