"""AddOnSubscription domain model."""
from datetime import timedelta
from vbwd.utils.datetime_utils import utcnow
from sqlalchemy.dialects.postgresql import UUID
from vbwd.extensions import db
from vbwd.models.base import BaseModel
from vbwd.models.enums import SubscriptionStatus


class AddOnSubscription(BaseModel):
    """
    Add-on subscription model.

    Tracks user subscriptions to add-ons.
    Linked to parent subscription and remains PENDING until payment is confirmed.
    """

    __tablename__ = "vbwd_addon_subscription"

    user_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("vbwd_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    addon_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("vbwd_addon.id"),
        nullable=False,
        index=True,
    )
    subscription_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("vbwd_subscription.id"),
        nullable=True,
        index=True,
    )
    invoice_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("vbwd_user_invoice.id"),
        nullable=True,
        index=True,
    )
    status = db.Column(
        db.Enum(
            SubscriptionStatus,
            name="subscriptionstatus",
            native_enum=True,
            create_constraint=False,
        ),
        nullable=False,
        default=SubscriptionStatus.PENDING,
        index=True,
    )
    starts_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    provider_subscription_id = db.Column(db.String(255), nullable=True, index=True)

    # Relationships
    addon = db.relationship(
        "AddOn",
        backref="subscriptions",
        lazy="joined",
    )

    @property
    def is_valid(self) -> bool:
        """Check if add-on subscription is currently valid."""
        if self.status != SubscriptionStatus.ACTIVE:
            return False
        if self.expires_at and self.expires_at < utcnow():
            return False
        return True

    def activate(self, duration_days: int) -> None:
        """Activate add-on subscription."""
        now = utcnow()
        self.status = SubscriptionStatus.ACTIVE
        self.starts_at = now
        self.expires_at = now + timedelta(days=duration_days)

    def cancel(self) -> None:
        """Cancel add-on subscription."""
        self.status = SubscriptionStatus.CANCELLED
        self.cancelled_at = utcnow()

    def expire(self) -> None:
        """Mark add-on subscription as expired."""
        self.status = SubscriptionStatus.EXPIRED

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "addon_id": str(self.addon_id),
            "subscription_id": str(self.subscription_id)
            if self.subscription_id
            else None,
            "invoice_id": str(self.invoice_id) if self.invoice_id else None,
            "status": self.status.value,
            "is_valid": self.is_valid,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "cancelled_at": self.cancelled_at.isoformat()
            if self.cancelled_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<AddOnSubscription(addon_id={self.addon_id}, status={self.status.value})>"
        )
