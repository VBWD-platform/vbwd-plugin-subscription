"""Integration — ``CatalogReadModel.plan_prices_by_ids`` bulk price projection.

Catalog consumers (ghrm) enrich their cards with the linked tariff plan's price
without importing subscription models: they read it through the subscription-owned
``CatalogReadModel``. This pins the new bulk read:

  * a block keyed by ``str(plan_id)`` carrying ``gross_amount`` + nested
    ``price`` (with ``currency``) + ``billing_period`` for a priced plan,
  * empty ids ⇒ ``{}`` (no query),
  * an unknown id ⇒ absent (caller reads absence as "no price"),
  * the whole page's plans are loaded in ONE SELECT against the plan table
    (no N+1).

Data is seeded through the ORM models (never raw SQL); each test runs inside the
rolled-back ``db`` transaction (self-cleaning, no wipe). The ``Price`` is computed
by the core ``PriceFactory`` (resolved from the container) and serialised by the
core ``build_pricing_block`` — the read model only calls those seams.

Engineering requirements (binding, restated): TDD-first; DevOps-first;
SOLID/DI/DRY; Liskov (one bad plan never kills the batch); no overengineering.
Quality guard: ``bin/pre-commit-check.sh --plugin subscription --full``.
"""
import re
import uuid
from decimal import Decimal

from sqlalchemy import event

from vbwd.models.enums import BillingPeriod
from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from plugins.subscription.subscription.services.catalog_read_model import (
    CatalogReadModel,
)

_PLAN_TABLE_SELECT = re.compile(r"\bfrom subscription_tarif_plan\b")


def _make_plan(db, *, price="29.99", billing=BillingPeriod.MONTHLY) -> TarifPlan:
    plan = TarifPlan(
        id=uuid.uuid4(),
        name=f"Plan {uuid.uuid4().hex[:8]}",
        slug=f"plan-{uuid.uuid4().hex[:8]}",
        price=Decimal(price) if price is not None else None,
        billing_period=billing,
        is_active=True,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def test_returns_price_block_keyed_by_str_id(db):
    plan = _make_plan(db, price="29.99")

    prices = CatalogReadModel().plan_prices_by_ids([plan.id])

    assert str(plan.id) in prices
    block = prices[str(plan.id)]
    assert block["gross_amount"] == "29.99"
    assert block["price"]["currency"] == "EUR"
    assert block["billing_period"] == BillingPeriod.MONTHLY.value
    assert block["display_price"] == 29.99


def test_empty_ids_returns_empty_dict(db):
    assert CatalogReadModel().plan_prices_by_ids([]) == {}


def test_unknown_id_is_absent(db):
    unknown = uuid.uuid4()

    prices = CatalogReadModel().plan_prices_by_ids([unknown])

    assert str(unknown) not in prices
    assert prices == {}


def test_absent_id_mixed_with_known_id(db):
    plan = _make_plan(db)
    unknown = uuid.uuid4()

    prices = CatalogReadModel().plan_prices_by_ids([plan.id, unknown])

    assert str(plan.id) in prices
    assert str(unknown) not in prices


def _count_plan_queries(db, plan_ids) -> int:
    """Number of SELECTs touching the plan table during one price resolution."""
    engine = db.session.get_bind()
    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        if _PLAN_TABLE_SELECT.search(statement.lower()):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        prices = CatalogReadModel().plan_prices_by_ids(plan_ids)
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    assert all(str(pid) in prices for pid in plan_ids)
    return len(statements)


def test_no_n_plus_1_query_count_is_constant(db):
    """Plan-table query count must not grow with the batch size (no N+1).

    ``selectin`` eager-loads the plan's taxes relationship, so a batch does a
    small CONSTANT number of SELECTs — never one per plan. Doubling the batch
    must not change the count.
    """
    first, second = _make_plan(db), _make_plan(db)
    count_two = _count_plan_queries(db, [first.id, second.id])

    third, fourth = _make_plan(db), _make_plan(db)
    count_four = _count_plan_queries(db, [first.id, second.id, third.id, fourth.id])

    assert count_two == count_four
