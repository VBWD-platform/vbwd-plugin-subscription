"""Integration: POST /api/v1/user/checkout applies a coupon discount.

Drives the full route → CheckoutRequestedEvent → checkout handler →
checkout_price_adjustment_registry path with the discount plugin's adjustment
registered, proving:
  - a valid SUBSCRIPTION coupon adds a negative discount line + reduces the
    invoice total, and increments the coupon's current_uses exactly once
  - an invalid coupon is rejected (4xx) with no invoice / no redemption
"""
from decimal import Decimal
from uuid import uuid4

import pytest

from vbwd.models.enums import BillingPeriod, UserRole, UserStatus
from vbwd.models.user import User


@pytest.fixture
def discount_ready(db):
    """Create discount tables + register the discount checkout adjustment.

    The discount plugin is an optional collaborator of subscription checkout, so
    this cross-plugin test runs only when it is installed (full local suite);
    in isolated plugin CI it is absent, so skip rather than error.
    """
    pytest.importorskip("plugins.discount.discount.models")
    import plugins.discount.discount.models  # noqa: F401

    db.create_all()
    from vbwd.services.checkout_price_adjustment_registry import (
        clear_price_adjustments,
        register_price_adjustment,
    )
    from plugins.discount.discount.checkout_adjustment import (
        checkout_price_adjustment,
    )

    register_price_adjustment("discount", checkout_price_adjustment)
    yield
    clear_price_adjustments()


def _make_user(db):
    user = User(
        id=uuid4(),
        email=f"checkout-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _make_plan(db, price="100.00"):
    from plugins.subscription.subscription.models.tarif_plan import TarifPlan

    plan = TarifPlan(
        id=uuid4(),
        name="Pro Plan",
        slug=f"pro-{uuid4().hex[:8]}",
        price=Decimal(price),
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def _make_coupon(db, *, code, scope, dtype, value):
    from plugins.discount.discount.models.coupon import Coupon
    from plugins.discount.discount.models.discount import DiscountRule
    from plugins.discount.discount.repositories.coupon_repository import (
        CouponRepository,
    )
    from plugins.discount.discount.repositories.discount_repository import (
        DiscountRepository,
    )

    discount = DiscountRepository(db.session).save(
        DiscountRule(
            id=uuid4(),
            name=f"D {code}",
            slug=f"d-{code.lower()}",
            discount_type=dtype,
            value=Decimal(value),
            scope=scope,
            is_active=True,
            priority=10,
        )
    )
    CouponRepository(db.session).save(
        Coupon(id=uuid4(), code=code, discount_id=discount.id, is_active=True)
    )


def _auth(monkeypatch, user):
    """Patch require_auth's collaborators so g.user is `user`."""
    from unittest.mock import MagicMock

    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = user
    svc = MagicMock()
    svc.verify_token.return_value = str(user.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: svc)


def test_checkout_with_subscription_coupon_reduces_total(
    db, client, discount_ready, monkeypatch
):
    from plugins.discount.discount.models.discount import DiscountType, DiscountScope

    user = _make_user(db)
    plan = _make_plan(db, price="100.00")
    _make_coupon(
        db,
        code="SUB30",
        scope=DiscountScope.SUBSCRIPTION,
        dtype=DiscountType.PERCENTAGE,
        value="30.00",
    )
    _auth(monkeypatch, user)

    resp = client.post(
        "/api/v1/user/checkout",
        json={"plan_id": str(plan.id), "coupon_code": "SUB30", "currency": "EUR"},
        headers={"Authorization": "Bearer valid"},
    )

    assert resp.status_code == 201, resp.get_json()
    invoice = resp.get_json()["invoice"]
    # 100.00 plan − 30% = 70.00 charged.
    assert Decimal(str(invoice["amount"])) == Decimal("70.00")

    line_items = invoice["line_items"]
    discount_lines = [li for li in line_items if Decimal(str(li["amount"])) < 0]
    assert len(discount_lines) == 1
    assert Decimal(str(discount_lines[0]["amount"])) == Decimal("-30.00")

    from plugins.discount.discount.repositories.coupon_repository import (
        CouponRepository,
    )

    assert CouponRepository(db.session).find_by_code("SUB30").current_uses == 1


def test_checkout_with_invalid_coupon_is_rejected(
    db, client, discount_ready, monkeypatch
):
    user = _make_user(db)
    plan = _make_plan(db, price="100.00")
    _auth(monkeypatch, user)

    resp = client.post(
        "/api/v1/user/checkout",
        json={"plan_id": str(plan.id), "coupon_code": "NOPE", "currency": "EUR"},
        headers={"Authorization": "Bearer valid"},
    )

    assert resp.status_code == 400, resp.get_json()
    # No invoice was created for this user.
    from vbwd.models.invoice import UserInvoice

    assert db.session.query(UserInvoice).filter_by(user_id=user.id).count() == 0
