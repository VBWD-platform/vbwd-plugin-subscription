"""TarifPlan domain model."""
from typing import Optional

from vbwd.extensions import db
from vbwd.models.base import BaseModel
from vbwd.models.enums import BillingPeriod

# S72.4: a per-plan netto/brutto price-display override. ``None`` inherits the
# global ``prices_display_mode`` core setting; ``"netto"``/``"brutto"`` override
# it. Kept in sync with the core ``PRICES_DISPLAY_MODES`` enum.
PRICE_DISPLAY_MODE_OVERRIDES = ("netto", "brutto")


def validate_price_display_mode(value: Optional[str]) -> Optional[str]:
    """Return ``value`` if it is a valid override, else raise ``ValueError``.

    ``None`` (inherit the global setting) and the two enum values are accepted;
    any other value is rejected so the admin route can map it to a 400.
    """
    if value is None or value in PRICE_DISPLAY_MODE_OVERRIDES:
        return value
    raise ValueError(
        "price_display_mode must be one of "
        f"{(None,) + PRICE_DISPLAY_MODE_OVERRIDES}, got {value!r}"
    )


# Many-to-many join to the CORE tax catalog (``vbwd_tax``). The FK uses
# ``ON DELETE RESTRICT`` so deleting a tax that is assigned to a plan is
# rejected by the database (S72.3) rather than silently dropping the link.
tarif_plan_tax = db.Table(
    "subscription_tarif_plan_tax",
    db.Column(
        "tarif_plan_id",
        db.UUID,
        db.ForeignKey("subscription_tarif_plan.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "tax_id",
        db.UUID,
        db.ForeignKey("vbwd_tax.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)


class TarifPlan(BaseModel):
    """
    Tariff plan model.

    Defines subscription plans with pricing and features.
    """

    __tablename__ = "subscription_tarif_plan"

    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(
        db.String(100),
        unique=True,
        nullable=False,
        index=True,
    )
    description = db.Column(db.Text)

    # S85.1 (D4): the single price double — full precision, never rounded in
    # code. The global ``default_currency`` setting (S84) is the currency, the
    # global ``prices_mode_in_db`` setting says how to interpret this number.
    price = db.Column(db.Float, nullable=True)

    billing_period = db.Column(
        db.Enum(
            BillingPeriod,
            name="billingperiod",
            native_enum=True,
            create_constraint=False,
        ),
        nullable=False,
    )
    features = db.Column(db.JSON, default=list)
    trial_days = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    sort_order = db.Column(db.Integer, default=0)

    # S72.4: per-plan netto/brutto price-display override. ``NULL`` inherits the
    # global ``prices_display_mode`` core setting; ``"netto"``/``"brutto"``
    # override it.
    price_display_mode = db.Column(db.String(8), nullable=True)

    # Vendor-mode (marketplace): the owning vendor's ``vbwd_user`` id. ``NULL`` is
    # a platform-owned plan (classic behaviour). Indexed for the vendor's "my
    # plans" filter; ``ON DELETE SET NULL`` reverts a removed vendor's plans to
    # the platform rather than cascading a catalog delete.
    vendor_id = db.Column(
        db.UUID,
        db.ForeignKey("vbwd_user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    subscriptions = db.relationship(
        "Subscription",
        backref="tarif_plan",
        lazy="dynamic",
        cascade="all, delete-orphan",
        foreign_keys="[Subscription.tarif_plan_id]",
    )
    pending_subscriptions = db.relationship(
        "Subscription",
        backref="pending_plan",
        lazy="dynamic",
        foreign_keys="[Subscription.pending_plan_id]",
    )
    # Assigned core taxes (M2M). When present these take precedence over the
    # country-based pricing breakdown (S72.3).
    taxes = db.relationship(
        "Tax",
        secondary=tarif_plan_tax,
        lazy="selectin",
    )
    # (Invoices no longer FK the plan — the link is the SUBSCRIPTION line item's
    # subscription, whose tarif_plan_id resolves the plan. No ORM relationship.)

    def _serialize_categories(self) -> list:
        """Safely serialize categories, returning [] if not loaded or unavailable."""
        try:
            cats = getattr(self, "categories", None)
            if cats is None:
                return []
            return [
                {
                    "id": str(c.id),
                    "name": c.name,
                    "slug": c.slug,
                    "is_single": c.is_single,
                }
                for c in cats
            ]
        except Exception:
            return []

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

    @property
    def raw_price(self) -> float:
        """The stored price as a float (the ``Priceable`` protocol member).

        The ``PriceFactory`` reads ``raw_price``, never ``price`` directly, so
        every sellable exposes the same accessor regardless of column name.
        """
        return float(self.price) if self.price is not None else 0.0

    @property
    def is_recurring(self) -> bool:
        """Check if this is a recurring subscription plan.

        A plan with no billing period is NOT recurring. Guarding ``None`` here
        avoids the ``None != BillingPeriod.ONE_TIME`` truthiness trap that let a
        spec-less plan be reported recurring (so the payment mode-check chose
        ``mode=subscription``) while ``recurring_billing_spec`` raised on
        ``None.value`` — the divergence that produced empty Stripe line_items.
        """
        if self.billing_period is None:
            return False
        return self.billing_period != BillingPeriod.ONE_TIME

    def to_dict(self) -> dict:
        """Convert to dictionary.

        S85.1 (D5): the single ``price`` double is the only stored money field —
        no ``currency`` (global ``default_currency``) and no ``price_float``
        mirror. The computed ``Price`` value object is assembled at the route
        layer via the ``PriceFactory`` (S85.2), not here — the model stays thin.
        """
        result = {
            "id": str(self.id),
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "price": self.raw_price,
            "billing_period": self.billing_period.value,
            "features": self.features,
            "trial_days": self.trial_days,
            "is_active": self.is_active,
            "is_recurring": self.is_recurring,
            "categories": self._serialize_categories(),
            "taxes": self._serialize_taxes(),
            "price_display_mode": self.price_display_mode,
            "vendor_id": str(self.vendor_id) if self.vendor_id else None,
        }
        result["tax_ids"] = [tax["id"] for tax in result["taxes"]]
        return result

    def __repr__(self) -> str:
        return f"<TarifPlan(slug='{self.slug}', price={self.price})>"
