"""S85.2 — subscription checkout charges ``Price.brutto`` and records breakdown.

The charged amount (invoice grand total + plan line gross) comes from
``PriceFactory(...).brutto`` (D8). The plan line item persists the netto +
per-tax breakdown (in ``extra_data``; invoice columns stay ``Numeric(10,2)``).
Flipping the global ``prices_mode_in_db`` changes the charged total for the SAME
stored plan-price double.
"""
from decimal import Decimal
from uuid import uuid4

import pytest

from vbwd.models.enums import BillingPeriod, UserRole, UserStatus
from vbwd.models.tax import Tax
from vbwd.models.user import User
from vbwd.services.core_settings_store import update_core_settings
from plugins.subscription.subscription.models.tarif_plan import TarifPlan


@pytest.fixture
def client(app):
    return app.test_client()


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


def _taxed_plan(db, stored_price):
    tax = Tax(name="VAT", code=f"VAT_{uuid4().hex[:6]}", rate=Decimal("19.00"))
    plan = TarifPlan(
        id=uuid4(),
        name="Pro Plan",
        slug=f"pro-{uuid4().hex[:8]}",
        price=float(stored_price),
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
    )
    db.session.add_all([tax, plan])
    db.session.flush()
    plan.taxes = [tax]
    db.session.commit()
    return plan


def _auth(monkeypatch, user):
    from unittest.mock import MagicMock

    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = user
    svc = MagicMock()
    svc.verify_token.return_value = str(user.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: svc)


def _checkout(client, plan):
    return client.post(
        "/api/v1/user/checkout",
        json={"plan_id": str(plan.id), "currency": "EUR"},
        headers={"Authorization": "Bearer valid"},
    )


def test_netto_mode_charges_gross_total(db, client, monkeypatch):
    update_core_settings({"prices_mode_in_db": "NETTO"})
    user = _make_user(db)
    plan = _taxed_plan(db, Decimal("100.00"))
    _auth(monkeypatch, user)

    resp = _checkout(client, plan)

    assert resp.status_code == 201, resp.get_json()
    assert Decimal(str(resp.get_json()["invoice"]["amount"])) == Decimal("119.00")


def test_brutto_mode_charges_stored_double_as_gross(db, client, monkeypatch):
    update_core_settings({"prices_mode_in_db": "BRUTTO"})
    user = _make_user(db)
    plan = _taxed_plan(db, Decimal("119.00"))
    _auth(monkeypatch, user)

    resp = _checkout(client, plan)

    assert resp.status_code == 201, resp.get_json()
    assert Decimal(str(resp.get_json()["invoice"]["amount"])) == Decimal("119.00")
    update_core_settings({"prices_mode_in_db": "NETTO"})


def test_mode_flip_changes_charged_total_for_same_double(db, client, monkeypatch):
    user = _make_user(db)

    update_core_settings({"prices_mode_in_db": "NETTO"})
    netto_plan = _taxed_plan(db, Decimal("100.00"))
    _auth(monkeypatch, user)
    netto_total = _checkout(client, netto_plan).get_json()["invoice"]["amount"]

    update_core_settings({"prices_mode_in_db": "BRUTTO"})
    brutto_plan = _taxed_plan(db, Decimal("100.00"))
    brutto_total = _checkout(client, brutto_plan).get_json()["invoice"]["amount"]

    assert Decimal(str(netto_total)) != Decimal(str(brutto_total))
    update_core_settings({"prices_mode_in_db": "NETTO"})


def test_plan_line_item_records_net_and_tax_breakdown(db, client, monkeypatch):
    update_core_settings({"prices_mode_in_db": "NETTO"})
    user = _make_user(db)
    plan = _taxed_plan(db, Decimal("100.00"))
    _auth(monkeypatch, user)

    resp = _checkout(client, plan)
    invoice = resp.get_json()["invoice"]

    plan_line = next(li for li in invoice["line_items"] if li["type"] == "SUBSCRIPTION")
    breakdown = plan_line["metadata"]["price_breakdown"]
    net = Decimal(str(breakdown["netto"]))
    tax_sum = sum(Decimal(str(tax["amount"])) for tax in breakdown["taxes"])
    gross = Decimal(str(plan_line["amount"]))
    assert (net + tax_sum).quantize(Decimal("0.01")) == gross.quantize(Decimal("0.01"))
