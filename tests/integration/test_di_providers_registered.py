"""Regression guard — the subscription plugin must register its repository
providers on the shared DI container.

These providers were extracted from core `vbwd/container.py`; if the plugin
stops re-registering them, the checkout / cancel handlers, line-item handlers,
and the stripe/paypal/yookassa payment + webhook routes all fail at runtime with
``'DynamicContainer' object has no attribute 'subscription_repository'`` — the
exact bug this test prevents (no test covered it after the extraction, so it
shipped broken). See report 04.
"""
import pytest

# Providers the subscription plugin owns and must add to the container. Core
# declares none of these.
REQUIRED_PROVIDERS = [
    "subscription_repository",
    "addon_subscription_repository",
    "addon_repository",
    "tarif_plan_repository",
    "tarif_plan_category_repository",
]


@pytest.mark.parametrize("provider_name", REQUIRED_PROVIDERS)
def test_provider_registered_on_container(app, provider_name):
    """Each subscription repository provider exists on the app container."""
    container = getattr(app, "container", None)
    assert container is not None, "app.container is not set"
    assert hasattr(container, provider_name), (
        f"container is missing provider '{provider_name}' — the subscription "
        f"plugin's on_enable() must register it"
    )


def test_providers_resolve_to_repositories(app, db):
    """Each provider resolves to a repository bound to the request session."""
    container = app.container
    with app.app_context():
        for provider_name in REQUIRED_PROVIDERS:
            repo = getattr(container, provider_name)()
            assert repo is not None, f"{provider_name} resolved to None"
            # BaseRepository subclasses expose the session they were built with.
            assert hasattr(repo, "session") or hasattr(repo, "_session"), (
                f"{provider_name} did not resolve to a repository instance"
            )
