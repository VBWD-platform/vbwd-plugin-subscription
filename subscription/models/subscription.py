"""Subscription domain model."""
from datetime import timedelta
from vbwd.utils.datetime_utils import utcnow
from sqlalchemy.dialects.postgresql import UUID
from vbwd.extensions import db
from vbwd.models.base import BaseModel
from vbwd.models.enums import SubscriptionStatus


class Subscription(BaseModel):
    """
    User subscription model.

    Tracks subscription lifecycle from creation through expiration.
    """

    __tablename__ = "subscription_record"

    user_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("vbwd_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tarif_plan_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("subscription_tarif_plan.id"),
        nullable=False,
    )
    pending_plan_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("subscription_tarif_plan.id"),
        nullable=True,
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
    started_at = db.Column(db.DateTime)
    expires_at = db.Column(db.DateTime, index=True)
    trial_end_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime)
    paused_at = db.Column(db.DateTime, nullable=True)
    payment_failed_at = db.Column(db.DateTime, nullable=True)
    provider_subscription_id = db.Column(
        db.String(255), unique=True, nullable=True, index=True
    )

    # Relationships
    # (Invoices link back via their SUBSCRIPTION line item — item_id == id —
    # not a column on the core invoice, so no ORM relationship here.)

    @property
    def is_valid(self) -> bool:
        """Check if subscription is currently valid (ACTIVE or TRIALING)."""
        if self.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING):
            return False
        if self.expires_at and self.expires_at < utcnow():
            return False
        return True

    @property
    def is_trialing(self) -> bool:
        """Check if subscription is in trial period."""
        return self.status == SubscriptionStatus.TRIALING

    @property
    def days_remaining(self) -> int:
        """Calculate days remaining until expiration."""
        if not self.expires_at:
            return 0
        delta = self.expires_at - utcnow()
        return max(0, delta.days)

    def start_trial(self, trial_days: int) -> None:
        """
        Start a trial period.

        Args:
            trial_days: Number of days for the trial.
        """
        now = utcnow()
        self.status = SubscriptionStatus.TRIALING
        self.started_at = now
        self.trial_end_at = now + timedelta(days=trial_days)
        self.expires_at = self.trial_end_at

    def activate(self, duration_days: int) -> None:
        """
        Activate subscription.

        Args:
            duration_days: Number of days the subscription is valid.
        """
        now = utcnow()
        self.status = SubscriptionStatus.ACTIVE
        self.started_at = now
        self.expires_at = now + timedelta(days=duration_days)

    def cancel(self) -> None:
        """Cancel subscription."""
        self.status = SubscriptionStatus.CANCELLED
        self.cancelled_at = utcnow()

    def expire(self) -> None:
        """Mark subscription as expired."""
        self.status = SubscriptionStatus.EXPIRED

    def pause(self) -> None:
        """Pause subscription."""
        self.status = SubscriptionStatus.PAUSED
        self.paused_at = utcnow()

    def resume(self) -> None:
        """Resume paused subscription."""
        self.status = SubscriptionStatus.ACTIVE
        self.paused_at = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "tarif_plan_id": self.tarif_plan_id,
            "pending_plan_id": self.pending_plan_id,
            "status": self.status.value,
            "is_valid": self.is_valid,
            "is_trialing": self.is_trialing,
            "days_remaining": self.days_remaining,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "trial_end_at": self.trial_end_at.isoformat()
            if self.trial_end_at
            else None,
            "cancelled_at": self.cancelled_at.isoformat()
            if self.cancelled_at
            else None,
            "paused_at": self.paused_at.isoformat() if self.paused_at else None,
        }

    def __repr__(self) -> str:
        return f"<Subscription(id={self.id}, user_id={self.user_id}, status={self.status.value})>"
