"""TarifPlan carries a nullable ``vendor_id`` and serialises it.

Ownership: a vendor-owned plan records the owning user's id; a platform-owned
plan leaves it ``None``. ``to_dict`` exposes it so the admin UI / marketplace
can read the owner.
"""
from uuid import uuid4

from vbwd.models.enums import BillingPeriod


def test_tarif_plan_to_dict_includes_vendor_id():
    from plugins.subscription.subscription.models import TarifPlan

    vendor_id = uuid4()
    plan = TarifPlan(
        id=uuid4(),
        name="Vendor Plan",
        slug=f"vp-{uuid4().hex[:8]}",
        price=10.0,
        billing_period=BillingPeriod.MONTHLY,
        vendor_id=vendor_id,
    )
    serialized = plan.to_dict()
    assert "vendor_id" in serialized
    assert serialized["vendor_id"] == str(vendor_id)


def test_tarif_plan_to_dict_vendor_id_none_for_platform_plan():
    from plugins.subscription.subscription.models import TarifPlan

    plan = TarifPlan(
        id=uuid4(),
        name="Platform Plan",
        slug=f"pp-{uuid4().hex[:8]}",
        price=10.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    assert plan.to_dict()["vendor_id"] is None
