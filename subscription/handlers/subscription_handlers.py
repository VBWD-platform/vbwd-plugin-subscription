"""Subscription event handlers."""
from vbwd.events.domain import IEventHandler, DomainEvent, EventResult
from vbwd.events.subscription_events import (
    SubscriptionActivatedEvent,
    SubscriptionCancelledEvent,
    PaymentCompletedEvent,
    PaymentFailedEvent,
)


class SubscriptionActivatedHandler(IEventHandler):
    """
    Handler for subscription activation events.

    This handler performs actions when a subscription is activated.
    """

    def __init__(self, email_service=None):
        """
        Initialize handler.

        Args:
            email_service: Optional EmailService for sending activation emails.
        """
        self.handled_events = []
        self._email_service = email_service

    def can_handle(self, event: DomainEvent) -> bool:
        """Check if this handler handles subscription.activated events."""
        return isinstance(event, SubscriptionActivatedEvent)

    def handle(self, event: DomainEvent) -> EventResult:
        """
        Handle subscription activation event.

        Args:
            event: SubscriptionActivatedEvent

        Returns:
            EventResult indicating success or failure
        """
        if not isinstance(event, SubscriptionActivatedEvent):
            return EventResult.error_result("Invalid event type")

        try:
            # Track handled events
            self.handled_events.append(event)

            email_sent = False

            # Send activation confirmation email if service available
            if self._email_service and event.user_email:
                result = self._email_service.send_subscription_activated(
                    to_email=event.user_email,
                    first_name=event.first_name or "User",
                    plan_name=event.plan_name or "Subscription",
                    expires_at=event.expires_at,
                )
                email_sent = result.success

            return EventResult.success_result(
                {
                    "subscription_id": str(event.subscription_id),
                    "user_id": str(event.user_id),
                    "email_sent": email_sent,
                    "handled": True,
                }
            )

        except Exception as e:
            return EventResult.error_result(str(e))


class SubscriptionCancelledHandler(IEventHandler):
    """
    Handler for subscription cancellation events.

    This handler performs actions when a subscription is cancelled.
    """

    def __init__(self, email_service=None):
        """
        Initialize handler.

        Args:
            email_service: Optional EmailService for sending cancellation emails.
        """
        self.handled_events = []
        self._email_service = email_service

    def can_handle(self, event: DomainEvent) -> bool:
        """Check if this handler handles subscription.cancelled events."""
        return isinstance(event, SubscriptionCancelledEvent)

    def handle(self, event: DomainEvent) -> EventResult:
        """
        Handle subscription cancellation event.

        Args:
            event: SubscriptionCancelledEvent

        Returns:
            EventResult indicating success or failure
        """
        if not isinstance(event, SubscriptionCancelledEvent):
            return EventResult.error_result("Invalid event type")

        try:
            # Track handled events
            self.handled_events.append(event)

            email_sent = False

            # Send cancellation confirmation email if service available
            if self._email_service and event.user_email:
                result = self._email_service.send_subscription_cancelled(
                    to_email=event.user_email,
                    first_name=event.first_name or "User",
                    plan_name=event.plan_name or "Subscription",
                )
                email_sent = result.success

            return EventResult.success_result(
                {
                    "subscription_id": str(event.subscription_id),
                    "user_id": str(event.user_id),
                    "email_sent": email_sent,
                    "handled": True,
                }
            )

        except Exception as e:
            return EventResult.error_result(str(e))


class PaymentCompletedHandler(IEventHandler):
    """
    Handler for payment completion events.

    This handler activates subscriptions when payment is completed.
    """

    def __init__(self, subscription_service=None, email_service=None):
        """
        Initialize handler.

        Args:
            subscription_service: Optional SubscriptionService for activation.
            email_service: Optional EmailService for sending payment receipts.
        """
        self.handled_events = []
        self.subscription_service = subscription_service
        self._email_service = email_service

    def can_handle(self, event: DomainEvent) -> bool:
        """Check if this handler handles payment.completed events."""
        return isinstance(event, PaymentCompletedEvent)

    def handle(self, event: DomainEvent) -> EventResult:
        """
        Handle payment completion event.

        Args:
            event: PaymentCompletedEvent

        Returns:
            EventResult indicating success or failure
        """
        if not isinstance(event, PaymentCompletedEvent):
            return EventResult.error_result("Invalid event type")

        try:
            # Track handled events
            self.handled_events.append(event)

            # Activate subscription if service available
            if self.subscription_service:
                self.subscription_service.activate_subscription(event.subscription_id)

            email_sent = False

            # Send payment receipt email if service available
            if self._email_service and event.user_email:
                amount_str = f"{event.amount} {event.currency}" if event.amount else ""
                result = self._email_service.send_payment_receipt(
                    to_email=event.user_email,
                    first_name=event.first_name or "User",
                    invoice_number=event.invoice_number or event.transaction_id,
                    amount=amount_str,
                )
                email_sent = result.success

            return EventResult.success_result(
                {
                    "subscription_id": str(event.subscription_id),
                    "transaction_id": event.transaction_id,
                    "activated": self.subscription_service is not None,
                    "email_sent": email_sent,
                    "handled": True,
                }
            )

        except Exception as e:
            return EventResult.error_result(str(e))


class PaymentFailedHandler(IEventHandler):
    """
    Handler for payment failure events.

    This handler performs actions when payment fails.
    """

    def __init__(self, email_service=None):
        """
        Initialize handler.

        Args:
            email_service: Optional EmailService for sending failure notifications.
        """
        self.handled_events = []
        self._email_service = email_service

    def can_handle(self, event: DomainEvent) -> bool:
        """Check if this handler handles payment.failed events."""
        return isinstance(event, PaymentFailedEvent)

    def handle(self, event: DomainEvent) -> EventResult:
        """
        Handle payment failure event.

        Args:
            event: PaymentFailedEvent

        Returns:
            EventResult indicating success or failure
        """
        if not isinstance(event, PaymentFailedEvent):
            return EventResult.error_result("Invalid event type")

        try:
            # Track handled events
            self.handled_events.append(event)

            email_sent = False

            # Send payment failure notification if service available
            if self._email_service and event.user_email:
                result = self._email_service.send_payment_failed(
                    to_email=event.user_email,
                    first_name=event.first_name or "User",
                    plan_name=event.plan_name or "Subscription",
                    retry_url=event.retry_url or "https://vbwd.com/retry",
                )
                email_sent = result.success

            return EventResult.success_result(
                {
                    "subscription_id": str(event.subscription_id),
                    "user_id": str(event.user_id),
                    "email_sent": email_sent,
                    "handled": True,
                }
            )

        except Exception as e:
            return EventResult.error_result(str(e))
