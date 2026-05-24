"""S4 — the subscription↔invoice link is the SUBSCRIPTION line item.

``InvoiceRepository.find_by_subscription`` must resolve a subscription's
invoices through the invoice's SUBSCRIPTION line item (item_id == subscription
id), now that the core invoice table carries no ``subscription_id`` column.
"""
from decimal import Decimal
from uuid import uuid4

from vbwd.models.enums import InvoiceStatus, LineItemType
from vbwd.models.invoice import UserInvoice
from vbwd.models.invoice_line_item import InvoiceLineItem
from vbwd.models.user import User
from vbwd.repositories.invoice_repository import InvoiceRepository


def _user(db) -> User:
    user = User(
        email=f"s4-{uuid4().hex}@example.com",
        password_hash="x",
    )
    db.session.add(user)
    db.session.flush()
    return user


def _invoice_with_line_item(user_id, item_type: LineItemType, item_id) -> UserInvoice:
    invoice = UserInvoice(
        user_id=user_id,
        invoice_number=UserInvoice.generate_invoice_number(),
        amount=Decimal("9.99"),
        currency="EUR",
        status=InvoiceStatus.PENDING,
    )
    invoice.line_items.append(
        InvoiceLineItem(
            item_type=item_type,
            item_id=item_id,
            description="Item",
            quantity=1,
            unit_price=Decimal("9.99"),
            total_price=Decimal("9.99"),
        )
    )
    return invoice


def test_find_by_subscription_matches_via_subscription_line_item(db):
    user = _user(db)
    subscription_id = uuid4()
    invoice = _invoice_with_line_item(
        user.id, LineItemType.SUBSCRIPTION, subscription_id
    )
    db.session.add(invoice)
    db.session.commit()

    found = InvoiceRepository(db.session).find_by_subscription(subscription_id)

    assert [inv.id for inv in found] == [invoice.id]


def test_find_by_subscription_ignores_other_subscriptions_and_types(db):
    user = _user(db)
    target = uuid4()
    matching = _invoice_with_line_item(user.id, LineItemType.SUBSCRIPTION, target)
    other_subscription = _invoice_with_line_item(
        user.id, LineItemType.SUBSCRIPTION, uuid4()
    )
    token_invoice = _invoice_with_line_item(user.id, LineItemType.TOKEN_BUNDLE, target)
    db.session.add_all([matching, other_subscription, token_invoice])
    db.session.commit()

    found = InvoiceRepository(db.session).find_by_subscription(target)

    assert [inv.id for inv in found] == [matching.id]
