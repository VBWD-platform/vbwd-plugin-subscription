"""AddOn domain model - optional extras for subscriptions."""
from vbwd.extensions import db
from vbwd.models.base import BaseModel
from vbwd.models.enums import BillingPeriod
from sqlalchemy.dialects.postgresql import JSONB

# Many-to-many junction table: addon <-> tarif_plan
addon_tarif_plans = db.Table(
    "subscription_addon_tarif_plans",
    db.Column(
        "addon_id",
        db.UUID,
        db.ForeignKey("subscription_addon.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "tarif_plan_id",
        db.UUID,
        db.ForeignKey("subscription_tarif_plan.id", ondelete="CASCADE"),
        primary_key=True,
        # SECOND column of the composite PK → the PK index can't serve a
        # ``WHERE tarif_plan_id = ?`` probe, so deleting a plan would seq-scan
        # this link heap once per deleted plan (O(N²) — the S89 t3 load-test
        # reset hang; same gap fixed for shop_product_category_link). Mirrored by
        # migration 20260617_sub_link_tarif_plan_id_idx for existing DBs.
        index=True,
    ),
)


# S85.1 (D6): many-to-many join to the CORE tax catalog (``vbwd_tax``), mirroring
# the S72.3 shape so add-ons carry a ``taxes`` relationship like plans /
# products / resources. The ``tax_id`` FK uses ``ON DELETE RESTRICT`` so deleting
# a tax assigned to an add-on is rejected by the database rather than silently
# dropping the link; ``addon_id`` uses ``ON DELETE CASCADE``.
addon_tax = db.Table(
    "subscription_addon_tax",
    db.Column(
        "addon_id",
        db.UUID,
        db.ForeignKey("subscription_addon.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "tax_id",
        db.UUID,
        db.ForeignKey("vbwd_tax.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)


class AddOn(BaseModel):
    """
    Add-on model.

    Represents optional extras that can be added to subscriptions.
    Uses a JSON `config` field for flexible parameters (like tarif_plan.features).

    Add-ons can optionally be bound to one or more tariff plans:
    - tarif_plans=[] → independent, visible to all users
    - tarif_plans=[plan_A, plan_B] → only visible to subscribers of those plans
    """

    __tablename__ = "subscription_addon"

    # Basic info
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)

    # Pricing — S85.1 (D4/D5): the single price double (full precision, never
    # rounded in code); the currency is the global ``default_currency`` (S84).
    price = db.Column(db.Float, nullable=False, default=0)
    billing_period = db.Column(
        db.String(50), nullable=False, default=BillingPeriod.MONTHLY.value
    )

    # Flexible configuration (like tarif_plan.features)
    config = db.Column(JSONB, nullable=False, default=dict)

    # Status
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    # Many-to-many relationship to tariff plans (optional restriction)
    tarif_plans = db.relationship(
        "TarifPlan",
        secondary=addon_tarif_plans,
        backref=db.backref("addons", lazy="dynamic"),
        lazy="selectin",
    )

    # Assigned core taxes (M2M, S85.1 / D6). Mirrors plan / product / resource.
    taxes = db.relationship(
        "Tax",
        secondary=addon_tax,
        lazy="selectin",
    )

    @property
    def raw_price(self) -> float:
        """The stored price as a float (the ``Priceable`` protocol member)."""
        return float(self.price) if self.price is not None else 0.0

    @property
    def is_recurring(self) -> bool:
        """Check if this is a recurring add-on."""
        return self.billing_period != BillingPeriod.ONE_TIME.value

    @property
    def is_independent(self) -> bool:
        """Check if this add-on is available to all users (not plan-restricted)."""
        return len(self.tarif_plans) == 0

    def _serialize_taxes(self) -> list:
        """Serialize assigned core taxes to ``{id, code, name, rate}``."""
        taxes = getattr(self, "taxes", None) or []
        return [
            {
                "id": str(tax.id),
                "code": tax.code,
                "name": tax.name,
                "rate": str(tax.rate),
            }
            for tax in taxes
        ]

    def to_dict(self) -> dict:
        """Convert to dictionary for API response.

        S85.1 (D5): a single ``price`` double — no ``currency`` (global
        ``default_currency``). The computed ``Price`` is assembled at the route
        layer (S85.2), keeping the model thin.
        """
        taxes = self._serialize_taxes()
        return {
            "id": str(self.id),
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "price": self.raw_price,
            "billing_period": self.billing_period,
            "config": self.config or {},
            "is_active": self.is_active,
            "is_recurring": self.is_recurring,
            "sort_order": self.sort_order,
            "tax_ids": [tax["id"] for tax in taxes],
            "taxes": taxes,
            "tarif_plan_ids": [str(tp.id) for tp in self.tarif_plans],  # type: ignore[attr-defined]
            "tarif_plans": [
                {"id": str(tp.id), "name": tp.name} for tp in self.tarif_plans  # type: ignore[attr-defined]
            ],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<AddOn(name='{self.name}', slug='{self.slug}', price={self.price})>"
