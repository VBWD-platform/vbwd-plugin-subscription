"""S4 — writers without a prior line item now attach a SUBSCRIPTION one.

``SubscriptionService.expire_trials`` used to link its invoice via the
``subscription_id`` column. With that column gone it must instead attach a
SUBSCRIPTION line item, so the invoice is still discoverable through
``InvoiceRepository.find_by_subscription``.
"""
from datetime import timedelta

from vbwd.models.enums import BillingPeriod, SubscriptionStatus
from vbwd.models.user import User
from vbwd.repositories.invoice_repository import InvoiceRepository
from vbwd.utils.datetime_utils import utcnow

from plugins.subscription.subscription.models import Subscription, TarifPlan
from plugins.subscription.subscription.repositories.subscription_repository import (
    SubscriptionRepository,
)
from plugins.subscription.subscription.services.subscription_service import (
    SubscriptionService,
)


def test_expire_trials_links_invoice_via_subscription_line_item(db):
    from uuid import uuid4

    user = User(email=f"trial-{uuid4().hex}@example.com", password_hash="x")
    plan = TarifPlan(
        name="Trial Plan",
        slug=f"trial-plan-{uuid4().hex}",
        price=12.50,
        is_active=True,
        billing_period=BillingPeriod.MONTHLY,
    )
    db.session.add_all([user, plan])
    db.session.flush()

    subscription = Subscription(
        user_id=user.id,
        tarif_plan_id=plan.id,
        status=SubscriptionStatus.TRIALING,
        started_at=utcnow() - timedelta(days=15),
        trial_end_at=utcnow() - timedelta(days=1),
    )
    db.session.add(subscription)
    db.session.commit()

    invoice_repo = InvoiceRepository(db.session)
    service = SubscriptionService(SubscriptionRepository(db.session))

    results = service.expire_trials(invoice_repo)
    db.session.commit()

    assert len(results) == 1
    found = invoice_repo.find_by_subscription(subscription.id)
    assert [str(inv.id) for inv in found] == [results[0]["invoice_id"]]
    assert found[0].amount == plan.price
