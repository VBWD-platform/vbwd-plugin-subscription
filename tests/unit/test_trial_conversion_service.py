"""S103.2d — TrialConversionService charges the saved method at trial-end.

On trial-end the service builds a PENDING renewal invoice and re-charges the
method the user selected at checkout:
  * success → capture already flipped the sub to ACTIVE; record "charged".
  * failure → cancel the sub, stamp ``payment_failed_at``, publish CANCELLED.
  * no charger (manual method) → cancel the sub, keep the invoice, no failure
    stamp; record "no_charger".
Collaborators are injected so the unit needs no Flask app.
"""
from types import SimpleNamespace
from uuid import uuid4

from vbwd.models.enums import SubscriptionStatus
from vbwd.plugins.payment_provider import ChargeResult
from vbwd.utils.datetime_utils import utcnow

from plugins.subscription.subscription.services.trial_conversion_service import (
    TrialConversionService,
)


class _FakeSubscription:
    def __init__(self, payment_method):
        self.id = uuid4()
        self.user_id = uuid4()
        self.status = SubscriptionStatus.TRIALING
        self.payment_method = payment_method
        self.payment_failed_at = None
        self.cancelled_at = None
        self.tarif_plan = SimpleNamespace(id=uuid4(), name="Plan A", slug="plan-a")

    def cancel(self):
        self.status = SubscriptionStatus.CANCELLED
        self.cancelled_at = utcnow()


class _FakeSubscriptionRepo:
    def __init__(self, expired):
        self._expired = expired
        self.saved = []

    def find_expired_trials(self, now=None):
        return list(self._expired)

    def save(self, subscription):
        self.saved.append(subscription)
        return subscription


class _FakeInvoiceRepo:
    def __init__(self):
        self.saved = []

    def save(self, invoice):
        invoice.id = invoice.id or uuid4()
        self.saved.append(invoice)
        return invoice


class _FakePriceFactory:
    def get_price_from_object(self, plan):
        # 10.00 gross, no taxes → net == gross.
        return SimpleNamespace(brutto=10.0, currency="EUR", taxes=[])


class _SuccessCharger:
    def __init__(self, subscription):
        self._subscription = subscription
        self.calls = []

    def charge_saved_method(self, *, user_id, invoice):
        self.calls.append((user_id, invoice))
        # Mirror production: capture activates the subscription synchronously.
        self._subscription.status = SubscriptionStatus.ACTIVE
        return ChargeResult(success=True, transaction_id="tx-1")


class _FailCharger:
    def __init__(self):
        self.calls = []

    def charge_saved_method(self, *, user_id, invoice):
        self.calls.append((user_id, invoice))
        return ChargeResult(success=False, error="insufficient_token_balance")


def _make_service(subscription_repo, charger_resolver, published):
    return TrialConversionService(
        subscription_repo=subscription_repo,
        invoice_repo=_FakeInvoiceRepo(),
        price_factory=_FakePriceFactory(),
        charger_resolver=charger_resolver,
        event_publisher=lambda name, sub, user_id: published.append(
            (name, sub, user_id)
        ),
    )


def test_successful_charge_keeps_subscription_active_and_records_charged():
    subscription = _FakeSubscription(payment_method="token_balance")
    repo = _FakeSubscriptionRepo([subscription])
    charger = _SuccessCharger(subscription)
    published = []
    service = _make_service(repo, lambda code: charger, published)

    results = service.convert_expired_trials(now=utcnow())

    assert len(results) == 1
    assert results[0]["outcome"] == "charged"
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert subscription.payment_failed_at is None
    # The service did not itself cancel.
    assert published == []
    # An invoice was created and the charger was called with it.
    assert len(service._invoice_repo.saved) == 1
    assert charger.calls[0][1] is service._invoice_repo.saved[0]
    assert charger.calls[0][0] == subscription.user_id


def test_failed_charge_cancels_stamps_and_publishes():
    subscription = _FakeSubscription(payment_method="token_balance")
    repo = _FakeSubscriptionRepo([subscription])
    charger = _FailCharger()
    published = []
    now = utcnow()
    service = _make_service(repo, lambda code: charger, published)

    results = service.convert_expired_trials(now=now)

    assert len(results) == 1
    assert results[0]["outcome"] == "charge_failed"
    assert results[0]["error"] == "insufficient_token_balance"
    assert subscription.status == SubscriptionStatus.CANCELLED
    assert subscription.payment_failed_at == now
    from plugins.subscription.subscription.services.lifecycle_events import (
        EVENT_SUBSCRIPTION_CANCELLED,
    )

    assert published == [
        (EVENT_SUBSCRIPTION_CANCELLED, subscription, subscription.user_id)
    ]


def test_no_charger_cancels_keeps_invoice_without_failure_stamp():
    subscription = _FakeSubscription(payment_method="invoice")
    repo = _FakeSubscriptionRepo([subscription])
    published = []
    service = _make_service(repo, lambda code: None, published)

    results = service.convert_expired_trials(now=utcnow())

    assert len(results) == 1
    assert results[0]["outcome"] == "no_charger"
    assert subscription.status == SubscriptionStatus.CANCELLED
    # Nothing was charged, so no payment-failure stamp.
    assert subscription.payment_failed_at is None
    # Invoice kept (created and saved) even with no charger.
    assert len(service._invoice_repo.saved) == 1
    # S69 D5: the lapsed trial still publishes CANCELLED so RBAC reconciles.
    from plugins.subscription.subscription.services.lifecycle_events import (
        EVENT_SUBSCRIPTION_CANCELLED,
    )

    assert published == [
        (EVENT_SUBSCRIPTION_CANCELLED, subscription, subscription.user_id)
    ]


def test_empty_trial_set_is_noop():
    repo = _FakeSubscriptionRepo([])
    published = []
    service = _make_service(repo, lambda code: None, published)
    assert service.convert_expired_trials() == []
