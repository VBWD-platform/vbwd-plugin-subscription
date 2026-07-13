"""Tests for GET /api/v1/subscription/config — public safe-config endpoint.

fe-user reads ``dashboard_plans_widget_slug`` from here (no auth) to decide
which ``TariffPlanCollection`` CMS widget renders the /dashboard/plans page.
Only explicitly-safe public keys are exposed; secrets never leak.
"""
from flask import Flask


def _make_app(config_override):
    """Throwaway Flask app hosting only the subscription blueprint + a
    ``config_store`` stub — fast and isolated from the full app factory.
    """
    from plugins.subscription.subscription.routes import subscription_bp

    flask_app = Flask(__name__)
    flask_app.register_blueprint(subscription_bp)

    class _Store:
        def get_config(self, plugin_name):
            assert plugin_name == "subscription"
            return dict(config_override)

    flask_app.config_store = _Store()  # type: ignore[attr-defined]
    return flask_app


class TestSubscriptionPublicConfigEndpoint:
    def test_returns_empty_slug_by_default(self):
        flask_app = _make_app({})

        with flask_app.test_client() as client:
            response = client.get("/api/v1/subscription/config")

        assert response.status_code == 200
        body = response.get_json()
        assert body["dashboard_plans_widget_slug"] == ""

    def test_reflects_config_store_override(self):
        flask_app = _make_app({"dashboard_plans_widget_slug": "my-plans"})

        with flask_app.test_client() as client:
            response = client.get("/api/v1/subscription/config")

        assert response.status_code == 200
        body = response.get_json()
        assert body["dashboard_plans_widget_slug"] == "my-plans"

    def test_exposes_only_whitelisted_public_keys(self):
        # A secret-ish private key present in the store must NOT be echoed.
        flask_app = _make_app(
            {
                "dashboard_plans_widget_slug": "plans",
                "checkout_draft_ttl_seconds": 900,
                "marketplace_enabled": True,
            }
        )

        with flask_app.test_client() as client:
            response = client.get("/api/v1/subscription/config")

        assert response.status_code == 200
        body = response.get_json()
        assert set(body.keys()) == {"dashboard_plans_widget_slug"}

    def test_is_public_no_auth_required(self):
        flask_app = _make_app({"dashboard_plans_widget_slug": "plans"})

        with flask_app.test_client() as client:
            response = client.get("/api/v1/subscription/config")

        assert response.status_code == 200
