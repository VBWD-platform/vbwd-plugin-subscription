"""AddOnSubscription repository implementation."""
from typing import List
from uuid import UUID
from vbwd.repositories.base import BaseRepository
from plugins.subscription.subscription.models import AddOnSubscription
from vbwd.models.enums import SubscriptionStatus


class AddOnSubscriptionRepository(BaseRepository[AddOnSubscription]):
    """Repository for AddOnSubscription entity operations."""

    def __init__(self, session):
        super().__init__(session=session, model=AddOnSubscription)

    def find_by_user(self, user_id: UUID) -> List[AddOnSubscription]:
        """Find all add-on subscriptions for a user."""
        return (
            self._session.query(AddOnSubscription)
            .filter(AddOnSubscription.user_id == user_id)
            .order_by(AddOnSubscription.created_at.desc())
            .all()
        )

    def find_by_subscription(self, subscription_id: UUID) -> List[AddOnSubscription]:
        """Find all add-on subscriptions for a parent subscription."""
        return (
            self._session.query(AddOnSubscription)
            .filter(AddOnSubscription.subscription_id == subscription_id)
            .all()
        )

    def find_by_invoice(self, invoice_id: UUID) -> List[AddOnSubscription]:
        """Find all add-on subscriptions for an invoice."""
        return (
            self._session.query(AddOnSubscription)
            .filter(AddOnSubscription.invoice_id == invoice_id)
            .all()
        )

    def find_by_provider_subscription_id(
        self, provider_subscription_id: str
    ) -> List[AddOnSubscription]:
        """Find add-on subscriptions by external provider subscription ID."""
        return (
            self._session.query(AddOnSubscription)
            .filter(
                AddOnSubscription.provider_subscription_id == provider_subscription_id
            )
            .all()
        )

    def find_active_by_user(self, user_id: UUID) -> List[AddOnSubscription]:
        """Find all active add-on subscriptions for a user."""
        return (
            self._session.query(AddOnSubscription)
            .filter(
                AddOnSubscription.user_id == user_id,
                AddOnSubscription.status == SubscriptionStatus.ACTIVE,
            )
            .all()
        )

    def find_pending_by_user(self, user_id: UUID) -> List[AddOnSubscription]:
        """Find all pending add-on subscriptions for a user."""
        return (
            self._session.query(AddOnSubscription)
            .filter(
                AddOnSubscription.user_id == user_id,
                AddOnSubscription.status == SubscriptionStatus.PENDING,
            )
            .all()
        )

    def create(self, addon_subscription: AddOnSubscription) -> AddOnSubscription:
        """Create a new add-on subscription."""
        self._session.add(addon_subscription)
        self._session.commit()
        self._session.refresh(addon_subscription)
        return addon_subscription
