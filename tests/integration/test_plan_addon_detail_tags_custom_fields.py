"""S77 — plan & add-on public detail serializers append tags + custom fields.

The fe-user tarif card and add-on card read the ``tags`` / ``custom_fields``
keys (plus the ``custom_field_defs`` for labels + type formatting) straight off
the payload, so ``GET /api/v1/tarif-plans/<slug>`` and ``GET /api/v1/addons/<id>``
must surface them. Opt-in via the core helper — no model import, no extra round
trip on the card. Mirrors the shop product detail wiring.
"""
from decimal import Decimal
from uuid import uuid4

from vbwd.models.enums import BillingPeriod
from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from plugins.subscription.subscription.models.addon import AddOn


def _make_plan(db, slug=None):
    plan = TarifPlan(
        id=uuid4(),
        name="Tagged Plan",
        slug=slug or f"tagged-plan-{uuid4().hex[:8]}",
        description="Plan with tags + custom fields",
        price=Decimal("29.99"),
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
        sort_order=0,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def _make_addon(db):
    addon = AddOn(
        id=uuid4(),
        name="Tagged Add-on",
        slug=f"tagged-addon-{uuid4().hex[:8]}",
        price=4.99,
        is_active=True,
    )
    db.session.add(addon)
    db.session.commit()
    return addon


def test_plan_detail_exposes_empty_tags_and_custom_fields_by_default(db, client):
    plan = _make_plan(db)

    body = client.get(f"/api/v1/tarif-plans/{plan.slug}").get_json()

    assert body["tags"] == []
    assert body["custom_fields"] == {}
    assert "custom_field_defs" in body


def test_plan_detail_exposes_attached_tags(app, db, client):
    plan = _make_plan(db)

    with app.app_context():
        app.container.tags_and_custom_fields().set_tags(
            "tarif_plan", plan.id, ["featured"]
        )

    body = client.get(f"/api/v1/tarif-plans/{plan.slug}").get_json()

    assert body["tags"] == ["featured"]


def test_addon_detail_exposes_empty_tags_and_custom_fields_by_default(db, client):
    addon = _make_addon(db)

    body = client.get(f"/api/v1/addons/{addon.id}").get_json()

    assert body["addon"]["tags"] == []
    assert body["addon"]["custom_fields"] == {}
    assert "custom_field_defs" in body["addon"]


def test_addon_detail_exposes_attached_tags(app, db, client):
    addon = _make_addon(db)

    with app.app_context():
        app.container.tags_and_custom_fields().set_tags("addon", addon.id, ["popular"])

    body = client.get(f"/api/v1/addons/{addon.id}").get_json()

    assert body["addon"]["tags"] == ["popular"]
