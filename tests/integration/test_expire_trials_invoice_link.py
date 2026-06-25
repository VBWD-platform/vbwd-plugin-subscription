"""S103.2 — trial-end now charges the selected method and converts the trial.

Replaces the legacy cancel-only ``SubscriptionService.expire_trials``. These
integration tests exercise ``TrialConversionService.convert_expired_trials``
against a real database:

  * a manual / unresolved method (no ``RecurringChargeProvider``) → the trial
    lapses to CANCELLED and the PENDING invoice is kept and linked via its
    SUBSCRIPTION line item (item_id == subscription.id);
  * a successful saved-method charge → the (faked) provider activates the sub,
    and the outcome is recorded as "charged".
"""
from datetime import timedelta
from uuid import uuid4

from vbwd.models.enums import BillingPeriod, SubscriptionStatus
from vbwd.models.user import User
from vbwd.plugins.payment_provider import ChargeResult
from vbwd.repositories.invoice_repository import InvoiceRepository
from vbwd.utils.datetime_utils import utcnow

from plugins.subscription.subscription.models import Subscription, TarifPlan
from plugins.subscription.subscription.repositories.subscription_repository import (
    SubscriptionRepository,
)
from plugins.subscription.subscription.services.trial_conversion_service import (
    TrialConversionService,
)


def _seed_expired_trial(db, *, payment_method):
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
        payment_method=payment_method,
    )
    db.session.add(subscription)
    db.session.commit()
    return subscription


def _service(db, app, charger_resolver):
    return TrialConversionService(
        subscription_repo=SubscriptionRepository(db.session),
        invoice_repo=InvoiceRepository(db.session),
        price_factory=app.container.price_factory(),
        charger_resolver=charger_resolver,
    )


def test_no_charger_cancels_and_links_pending_invoice(db, app):
    subscription = _seed_expired_trial(db, payment_method="invoice")
    service = _service(db, app, charger_resolver=lambda code: None)

    results = service.convert_expired_trials()
    db.session.commit()

    assert len(results) == 1
    assert results[0]["outcome"] == "no_charger"

    subscription_repo = SubscriptionRepository(db.session)
    reloaded = subscription_repo.find_by_id(subscription.id)
    assert reloaded.status == SubscriptionStatus.CANCELLED
    assert reloaded.payment_failed_at is None

    invoice_repo = InvoiceRepository(db.session)
    found = invoice_repo.find_by_subscription(subscription.id)
    assert [str(inv.id) for inv in found] == [results[0]["invoice_id"]]


def test_successful_charge_records_charged_and_keeps_active(db, app):
    subscription = _seed_expired_trial(db, payment_method="token_balance")

    class _Charger:
        def charge_saved_method(self, *, user_id, invoice):
            # Simulate the capture path activating the subscription.
            reloaded = SubscriptionRepository(db.session).find_by_id(subscription.id)
            reloaded.activate(30)
            db.session.flush()
            return ChargeResult(success=True, transaction_id="tx-1")

    service = _service(db, app, charger_resolver=lambda code: _Charger())

    results = service.convert_expired_trials()
    db.session.commit()

    assert len(results) == 1
    assert results[0]["outcome"] == "charged"

    reloaded = SubscriptionRepository(db.session).find_by_id(subscription.id)
    assert reloaded.status == SubscriptionStatus.ACTIVE
    assert reloaded.payment_failed_at is None
