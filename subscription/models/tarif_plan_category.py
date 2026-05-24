"""TarifPlanCategory domain model — hierarchical grouping for tariff plans."""
from __future__ import annotations
from typing import TYPE_CHECKING, List
from sqlalchemy.orm import Mapped, relationship
from vbwd.extensions import db
from vbwd.models.base import BaseModel

if TYPE_CHECKING:
    from plugins.subscription.subscription.models.tarif_plan import TarifPlan

# Many-to-many junction table: category <-> tarif_plan
tarif_plan_category_plans = db.Table(
    "vbwd_tarif_plan_category_plans",
    db.Column(
        "category_id",
        db.UUID,
        db.ForeignKey("vbwd_tarif_plan_category.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "tarif_plan_id",
        db.UUID,
        db.ForeignKey("vbwd_tarif_plan.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class TarifPlanCategory(BaseModel):
    """
    Tariff plan category model.

    Groups tariff plans into hierarchical categories.
    The `is_single` flag controls whether a user can hold one or many
    active subscriptions within this category.
    """

    __tablename__ = "vbwd_tarif_plan_category"

    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    parent_id = db.Column(
        db.UUID,
        db.ForeignKey("vbwd_tarif_plan_category.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_single = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    # Self-referential relationship
    children: Mapped[List[TarifPlanCategory]] = relationship(
        "TarifPlanCategory",
        backref=db.backref("parent", remote_side="TarifPlanCategory.id"),
        lazy="selectin",
    )

    # Many-to-many relationship to tariff plans
    tarif_plans: Mapped[List[TarifPlan]] = relationship(
        "TarifPlan",
        secondary=tarif_plan_category_plans,
        backref=db.backref("categories", lazy="selectin"),
        lazy="selectin",
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API response."""
        return {
            "id": str(self.id),
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "is_single": self.is_single,
            "sort_order": self.sort_order,
            "plan_count": len(self.tarif_plans),
            "children": [child.to_dict() for child in self.children],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<TarifPlanCategory(name='{self.name}', slug='{self.slug}', is_single={self.is_single})>"
