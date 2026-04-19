"""Subscription domain events."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID
from decimal import Decimal
from vbwd.events.domain import DomainEvent


@dataclass
class SubscriptionDunningEvent(DomainEvent):
    """Event: Dunning email should be sent for failed subscription payment."""

    subscription_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    days_overdue: int = 0

    def __post_init__(self):
        super().__post_init__()
        self.name = "subscription.dunning"
        if not hasattr(self, "data"):
            self.data = {}
        if not hasattr(self, "propagation_stopped"):
            self.propagation_stopped = False


@dataclass
class SubscriptionCreatedEvent(DomainEvent):
    """Event: New subscription was created."""

    subscription_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    tarif_plan_id: Optional[UUID] = None
    status: Optional[str] = None

    def __post_init__(self):
        """Set event name and timestamp."""
        super().__post_init__()
        self.name = "subscription.created"
        if not hasattr(self, "data"):
            self.data = {}
        if not hasattr(self, "propagation_stopped"):
            self.propagation_stopped = False


@dataclass
class SubscriptionActivatedEvent(DomainEvent):
    """Event: Subscription was activated."""

    subscription_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    tarif_plan_id: Optional[UUID] = None
    plan_id: Optional[UUID] = None  # Alias for tarif_plan_id
    plan_name: Optional[str] = None
    started_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    user_email: Optional[str] = None
    first_name: Optional[str] = None

    def __post_init__(self):
        """Set event name and timestamp."""
        super().__post_init__()
        self.name = "subscription.activated"
        if not hasattr(self, "data"):
            self.data = {}
        if not hasattr(self, "propagation_stopped"):
            self.propagation_stopped = False


@dataclass
class SubscriptionCancelledEvent(DomainEvent):
    """Event: Subscription was cancelled."""

    subscription_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    cancelled_by: Optional[UUID] = None
    reason: Optional[str] = None
    plan_name: Optional[str] = None
    user_email: Optional[str] = None
    first_name: Optional[str] = None

    def __post_init__(self):
        """Set event name and timestamp."""
        super().__post_init__()
        self.name = "subscription.cancelled"
        if not hasattr(self, "data"):
            self.data = {}
        if not hasattr(self, "propagation_stopped"):
            self.propagation_stopped = False


@dataclass
class SubscriptionExpiredEvent(DomainEvent):
    """Event: Subscription expired."""

    subscription_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    expired_at: Optional[datetime] = None

    def __post_init__(self):
        """Set event name and timestamp."""
        super().__post_init__()
        self.name = "subscription.expired"
        if not hasattr(self, "data"):
            self.data = {}
        if not hasattr(self, "propagation_stopped"):
            self.propagation_stopped = False


@dataclass
class PaymentCompletedEvent(DomainEvent):
    """Event: Payment was completed successfully."""

    subscription_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    transaction_id: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    invoice_number: Optional[str] = None
    user_email: Optional[str] = None
    first_name: Optional[str] = None

    def __post_init__(self):
        """Set event name and timestamp."""
        super().__post_init__()
        self.name = "payment.completed"
        if not hasattr(self, "data"):
            self.data = {}
        if not hasattr(self, "propagation_stopped"):
            self.propagation_stopped = False


@dataclass
class PaymentFailedEvent(DomainEvent):
    """Event: Payment failed."""

    subscription_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    error_message: Optional[str] = None
    plan_name: Optional[str] = None
    user_email: Optional[str] = None
    first_name: Optional[str] = None
    retry_url: Optional[str] = None

    def __post_init__(self):
        """Set event name and timestamp."""
        super().__post_init__()
        self.name = "payment.failed"
        if not hasattr(self, "data"):
            self.data = {}
        if not hasattr(self, "propagation_stopped"):
            self.propagation_stopped = False


"""Checkout domain events."""
from dataclasses import dataclass, field
from typing import List, Optional
from uuid import UUID
from vbwd.events.domain import DomainEvent


@dataclass
class CheckoutRequestedEvent(DomainEvent):
    """
    Event emitted when user requests checkout.

    Creates pending subscription, token bundle purchases, and add-on subscriptions.
    All items remain pending until payment is confirmed.
    """

    user_id: Optional[UUID] = None
    plan_id: Optional[UUID] = None
    token_bundle_ids: List[UUID] = field(default_factory=list)
    add_on_ids: List[UUID] = field(default_factory=list)
    currency: str = "USD"
    payment_method_code: Optional[str] = None

    def __post_init__(self):
        self.name = "checkout.requested"
        super().__post_init__()


@dataclass
class CheckoutCompletedEvent(DomainEvent):
    """
    Event emitted when checkout is successfully created.

    Contains IDs of all created pending items.
    """

    user_id: Optional[UUID] = None
    subscription_id: Optional[UUID] = None
    invoice_id: Optional[UUID] = None
    token_bundle_purchase_ids: List[UUID] = field(default_factory=list)
    addon_subscription_ids: List[UUID] = field(default_factory=list)

    def __post_init__(self):
        self.name = "checkout.completed"
        super().__post_init__()


@dataclass
class CheckoutFailedEvent(DomainEvent):
    """Event emitted when checkout creation fails."""

    user_id: Optional[UUID] = None
    error: Optional[str] = None
    error_type: Optional[str] = None

    def __post_init__(self):
        self.name = "checkout.failed"
        super().__post_init__()
