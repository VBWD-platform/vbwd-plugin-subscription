"""Integration tests for the recurring-billing subscriber (S50.4).

Exercises the renewal path against a real DB: a published recurring-charge fact
must create a renewal invoice for the matching subscription (linked via a
SUBSCRIPTION line item), and a duplicate provider reference must dedup.
"""
from decimal import Decimal
from uuid import uuid4

from vbwd.models.enums import BillingPeriod, LineItemType, SubscriptionStatus
from vbwd.models.user import User
from vbwd.repositories.invoice_repository import InvoiceRepository

from plugins.subscription.subscription.models import Subscription, TarifPlan
from plugins.subscription.subscription.handlers.recurring_billing_subscriber import (
    RecurringBillingSubscriber,
)


def _subscriber(db):
    """Subscriber whose repos use the test session (not request-scoped db)."""
    from plugins.subscription.subscription.repositories.subscription_repository import (
        SubscriptionRepository,
    )

    subscriber = RecurringBillingSubscriber()
    subscriber._subscription_repo = lambda: SubscriptionRepository(db.session)
    subscriber._invoice_repo = lambda: InvoiceRepository(db.session)
    return subscriber


def _subscription_with_provider_ref(db, provider_ref_id):
    user = User(email=f"renew-{uuid4().hex}@example.com", password_hash="x")
    plan = TarifPlan(
        name="Pro Plan",
        slug=f"pro-{uuid4().hex}",
        price=9.99,
        is_active=True,
        billing_period=BillingPeriod.MONTHLY,
    )
    db.session.add_all([user, plan])
    db.session.flush()

    subscription = Subscription(
        user_id=user.id,
        tarif_plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        provider_subscription_id=provider_ref_id,
    )
    db.session.add(subscription)
    db.session.commit()
    return subscription


def test_create_renewal_invoice_links_via_subscription_line_item(db):
    subscription = _subscription_with_provider_ref(db, "sub_ref_renew")
    subscriber = _subscriber(db)

    renewal_invoice_id = subscriber._create_renewal_invoice(
        provider="stripe",
        provider_ref_id="sub_ref_renew",
        amount=Decimal("9.99"),
        currency="eur",
        provider_reference="in_renew_1",
    )
    db.session.commit()

    assert renewal_invoice_id is not None
    invoice_repo = InvoiceRepository(db.session)
    found = invoice_repo.find_by_subscription(subscription.id)
    assert [inv.id for inv in found] == [renewal_invoice_id]
    invoice = invoice_repo.find_by_id(renewal_invoice_id)
    assert invoice.amount == Decimal("9.99")
    assert invoice.currency == "EUR"
    assert invoice.payment_method == "stripe"
    assert invoice.provider_session_id == "in_renew_1"
    assert any(
        li.item_type == LineItemType.SUBSCRIPTION and li.item_id == subscription.id
        for li in invoice.line_items
    )


def test_create_renewal_invoice_dedups_on_provider_reference(db):
    _subscription_with_provider_ref(db, "sub_ref_dup")
    subscriber = _subscriber(db)

    first_id = subscriber._create_renewal_invoice(
        provider="stripe",
        provider_ref_id="sub_ref_dup",
        amount=Decimal("9.99"),
        currency="eur",
        provider_reference="in_dup_1",
    )
    db.session.commit()
    second_id = subscriber._create_renewal_invoice(
        provider="stripe",
        provider_ref_id="sub_ref_dup",
        amount=Decimal("9.99"),
        currency="eur",
        provider_reference="in_dup_1",
    )
    db.session.commit()

    assert first_id == second_id


def test_create_renewal_invoice_no_match_returns_none(db):
    subscriber = _subscriber(db)
    result = subscriber._create_renewal_invoice(
        provider="stripe",
        provider_ref_id="does_not_exist",
        amount=Decimal("9.99"),
        currency="eur",
        provider_reference="in_orphan",
    )
    assert result is None


def test_provider_linked_records_ref_id(db):
    from vbwd.models.invoice import UserInvoice
    from vbwd.models.invoice_line_item import InvoiceLineItem
    from vbwd.models.enums import InvoiceStatus

    subscription = _subscription_with_provider_ref(db, None)
    invoice = UserInvoice(
        user_id=subscription.user_id,
        invoice_number=UserInvoice.generate_invoice_number(),
        amount=Decimal("9.99"),
        currency="EUR",
        status=InvoiceStatus.PENDING,
    )
    invoice.line_items.append(
        InvoiceLineItem(
            item_type=LineItemType.SUBSCRIPTION,
            item_id=subscription.id,
            description="Pro Plan",
            quantity=1,
            unit_price=Decimal("9.99"),
            total_price=Decimal("9.99"),
        )
    )
    db.session.add(invoice)
    db.session.commit()

    subscriber = _subscriber(db)
    subscriber.on_provider_linked(
        "payment.provider_linked",
        {
            "invoice_id": str(invoice.id),
            "provider": "stripe",
            "provider_ref_id": "sub_linked_ref",
        },
    )
    db.session.commit()

    refreshed = db.session.get(Subscription, subscription.id)
    assert refreshed.provider_subscription_id == "sub_linked_ref"
