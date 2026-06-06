"""S50.6 — the subscription plugin contributes its frontend event types.

Core no longer hardcodes `subscription:*` in the frontend-event whitelist; the
subscription plugin registers them into the core
``frontend_event_type_registry``. This test pins the contributed set and proves
the core ``/events/types`` view then lists them.
"""
import pytest

from vbwd.services.frontend_event_type_registry import (
    allowed_frontend_event_types,
    clear_frontend_event_types,
)
from plugins.subscription import SUBSCRIPTION_FRONTEND_EVENT_TYPES
from plugins.subscription import register_subscription_frontend_event_types


EXPECTED = {
    "subscription:created",
    "subscription:activated",
    "subscription:upgraded",
    "subscription:downgraded",
    "subscription:cancelled",
    "subscription:expired",
}


@pytest.fixture(autouse=True)
def _isolate():
    clear_frontend_event_types()
    yield
    clear_frontend_event_types()


def test_contributed_set_is_the_subscription_lifecycle_types():
    assert SUBSCRIPTION_FRONTEND_EVENT_TYPES == EXPECTED


def test_registration_adds_them_to_the_core_allowed_set():
    assert not (EXPECTED & allowed_frontend_event_types())

    register_subscription_frontend_event_types()

    assert EXPECTED <= allowed_frontend_event_types()
