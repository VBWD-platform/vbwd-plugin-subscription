"""Checkout stamps the vendor id on the buyer invoice line (the money path).

When ``marketplace_enabled`` is True and the purchased plan is vendor-owned, the
created invoice line's ``extra_data`` carries ``vendor_id`` = the vendor's user
id (the documented convention ``marketplace`` credits from). When the flag is
False — or the plan is platform-owned — no stamp is written (classic behaviour
unchanged).
"""
from uuid import uuid4

from vbwd.models.enums import BillingPeriod
from vbwd.models.invoice_line_item import InvoiceLineItem
from vbwd.models.user import User

from plugins.subscription.subscription.events import CheckoutRequestedEvent
from plugins.subscription.subscription.handlers import (
    checkout_handler as handler_module,
)
from plugins.subscription.subscription.handlers.checkout_handler import CheckoutHandler
from plugins.subscription.subscription.models import TarifPlan


def _make_user(db, *, prefix="user"):
    user = User(email=f"{prefix}-{uuid4().hex}@example.com", password_hash="x")
    db.session.add(user)
    db.session.commit()
    return user


def _plan(db, *, vendor_id):
    plan = TarifPlan(
        id=uuid4(),
        name="Vendor Plan",
        slug=f"vend-{uuid4().hex[:8]}",
        price=10.0,
        is_active=True,
        billing_period=BillingPeriod.MONTHLY,
        vendor_id=vendor_id,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def _subscription_line(db, invoice_id):
    from vbwd.models.enums import LineItemType

    return (
        db.session.query(InvoiceLineItem)
        .filter_by(invoice_id=invoice_id, item_type=LineItemType.SUBSCRIPTION)
        .first()
    )


def _checkout(app, db, buyer, plan):
    handler = CheckoutHandler(app.container)
    event = CheckoutRequestedEvent(
        user_id=buyer.id,
        plan_id=plan.id,
        currency="EUR",
        payment_method_code="token_balance",
    )
    result = handler.handle(event)
    db.session.commit()
    assert result.success, result.error
    return result.data["invoice"]["id"]


def test_checkout_stamps_vendor_id_when_enabled(app, db, monkeypatch):
    vendor_id = _make_user(db, prefix="vendor").id
    buyer = _make_user(db, prefix="buyer")
    plan = _plan(db, vendor_id=vendor_id)
    monkeypatch.setattr(handler_module, "marketplace_enabled", lambda: True)

    invoice_id = _checkout(app, db, buyer, plan)

    line = _subscription_line(db, invoice_id)
    assert line.extra_data.get("vendor_id") == str(vendor_id)


def test_checkout_does_not_stamp_when_disabled(app, db, monkeypatch):
    vendor_id = _make_user(db, prefix="vendor").id
    buyer = _make_user(db, prefix="buyer")
    plan = _plan(db, vendor_id=vendor_id)
    monkeypatch.setattr(handler_module, "marketplace_enabled", lambda: False)

    invoice_id = _checkout(app, db, buyer, plan)

    line = _subscription_line(db, invoice_id)
    assert "vendor_id" not in (line.extra_data or {})


def test_checkout_platform_plan_never_stamped(app, db, monkeypatch):
    buyer = _make_user(db, prefix="buyer")
    plan = _plan(db, vendor_id=None)
    monkeypatch.setattr(handler_module, "marketplace_enabled", lambda: True)

    invoice_id = _checkout(app, db, buyer, plan)

    line = _subscription_line(db, invoice_id)
    assert "vendor_id" not in (line.extra_data or {})
