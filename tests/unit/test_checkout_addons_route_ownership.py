"""S2 — /checkout + user /addons move from core user_bp to the plugin.

E2 (behaviour-preserving relocation): the URLs and their auth protection are
identical before and after; only the owning blueprint changes from core
`user` to the `subscription` plugin. The ownership assertion is the
agnosticism RED→GREEN; the others are move-invariant guards.
"""
import pytest

USER_SCOPED_SUBSCRIPTION_RULES = [
    "/api/v1/user/checkout",
    "/api/v1/user/addons",
    "/api/v1/user/addons/<addon_sub_id>",
    "/api/v1/user/addons/<addon_sub_id>/cancel",
]


def _rules_for(app, path):
    return [r for r in app.url_map.iter_rules() if str(r) == path]


@pytest.mark.parametrize("path", USER_SCOPED_SUBSCRIPTION_RULES)
def test_route_still_registered(app, path):
    """The URL must keep existing (move-invariant)."""
    assert _rules_for(app, path), f"{path} is no longer routed"


@pytest.mark.parametrize("path", USER_SCOPED_SUBSCRIPTION_RULES)
def test_route_owned_by_subscription_plugin_not_core_user(app, path):
    """Core `user` blueprint must not own subscription/checkout routes;
    the `subscription` plugin blueprint must."""
    endpoints = {r.endpoint for r in _rules_for(app, path)}
    assert endpoints, f"{path} not routed"
    for endpoint in endpoints:
        assert not endpoint.startswith(
            "user."
        ), f"{path} still served by core user_bp ({endpoint})"
        assert endpoint.startswith(
            "subscription."
        ), f"{path} not served by the subscription plugin ({endpoint})"


def test_checkout_requires_auth(client):
    """Route stays auth-protected after the move (no token ⇒ 401)."""
    response = client.post("/api/v1/user/checkout", json={})
    assert response.status_code == 401
