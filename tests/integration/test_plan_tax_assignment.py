"""S72.3 — plan↔tax M2M persistence + FK ON DELETE RESTRICT (integration, real PG).

Covers the contract end-to-end against the real schema:
- assigning ``tax_ids`` persists the M2M (replace-set, dedupe),
- ``to_dict()`` reflects assigned ``tax_ids``/``taxes``,
- deleting a tax that is referenced by a plan is blocked by the DB
  (``ON DELETE RESTRICT`` → IntegrityError), not silently cascaded.
"""
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from vbwd.models.tax import Tax
from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)


def _tax(db, code: str, rate: str = "19.00", is_active: bool = True) -> Tax:
    tax = Tax(
        name=f"Tax {code}",
        code=code,
        rate=Decimal(rate),
        is_active=is_active,
    )
    db.session.add(tax)
    db.session.flush()
    return tax


def _plan(db, slug: str) -> TarifPlan:
    plan = TarifPlan(
        name=slug.title(),
        slug=slug,
        price=Decimal("100.00"),
        billing_period="MONTHLY",
    )
    db.session.add(plan)
    db.session.flush()
    return plan


def test_assign_taxes_persists_m2m_and_to_dict_reflects_it(db):
    vat = _tax(db, f"VAT_{uuid4().hex[:6]}", "19.00")
    reduced = _tax(db, f"RED_{uuid4().hex[:6]}", "7.00")
    plan = _plan(db, f"pro-{uuid4().hex[:6]}")

    plan.taxes = [vat, reduced]
    db.session.commit()

    reloaded = TarifPlanRepository(db.session).find_by_id(plan.id)
    assert {t.id for t in reloaded.taxes} == {vat.id, reduced.id}
    data = reloaded.to_dict()
    assert set(data["tax_ids"]) == {str(vat.id), str(reduced.id)}
    assert {t["code"] for t in data["taxes"]} == {vat.code, reduced.code}


def test_replace_set_swaps_assigned_taxes(db):
    first = _tax(db, f"A_{uuid4().hex[:6]}")
    second = _tax(db, f"B_{uuid4().hex[:6]}")
    plan = _plan(db, f"swap-{uuid4().hex[:6]}")

    plan.taxes = [first]
    db.session.commit()

    # Replace-set: the new assignment fully supersedes the old one.
    reloaded = TarifPlanRepository(db.session).find_by_id(plan.id)
    reloaded.taxes = [second]
    db.session.commit()

    again = TarifPlanRepository(db.session).find_by_id(plan.id)
    assert {t.id for t in again.taxes} == {second.id}


def test_deleting_in_use_tax_is_blocked_by_restrict(db):
    vat = _tax(db, f"INUSE_{uuid4().hex[:6]}")
    plan = _plan(db, f"inuse-{uuid4().hex[:6]}")
    plan.taxes = [vat]
    db.session.commit()

    db.session.delete(vat)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    # The plan still references the tax — nothing was cascaded.
    reloaded = TarifPlanRepository(db.session).find_by_id(plan.id)
    assert {t.id for t in reloaded.taxes} == {vat.id}
