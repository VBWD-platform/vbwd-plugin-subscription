"""Subscription plugin — plans, subscriptions, add-ons, categories, checkout."""
from vbwd.plugins.base import BasePlugin, PluginMetadata


DEFAULT_CONFIG = {
    "trial_days": 14,
    "dunning_intervals_days": [3, 7],
    "expiration_check_interval_seconds": 60,
    "max_subscriptions_per_user": 10,
    "allow_downgrade": True,
    "proration_enabled": True,
}


class SubscriptionPlugin(BasePlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="subscription",
            version="1.0.0",
            author="VBWD",
            description="Subscription management — tarif plans, subscriptions, add-ons, checkout",
            dependencies=["email"],
        )

    def initialize(self, config=None):
        merged = {**DEFAULT_CONFIG}
        if config:
            merged.update(config)
        super().initialize(merged)

    def get_blueprint(self):
        from plugins.subscription.subscription.routes import subscription_bp

        return subscription_bp

    def get_url_prefix(self) -> str:
        return ""

    @property
    def admin_permissions(self):
        return [
            {
                "key": "subscription.plans.view",
                "label": "View plans",
                "group": "Subscriptions",
            },
            {
                "key": "subscription.plans.manage",
                "label": "Manage plans",
                "group": "Subscriptions",
            },
            {
                "key": "subscription.subscriptions.view",
                "label": "View subscriptions",
                "group": "Subscriptions",
            },
            {
                "key": "subscription.subscriptions.manage",
                "label": "Manage subscriptions",
                "group": "Subscriptions",
            },
            {
                "key": "subscription.addons.manage",
                "label": "Manage add-ons",
                "group": "Subscriptions",
            },
            {
                "key": "subscription.configure",
                "label": "Subscription settings",
                "group": "Subscriptions",
            },
        ]

    @property
    def user_permissions(self):
        return [
            {
                "key": "subscription.plans.view",
                "label": "View available plans",
                "group": "Subscription",
            },
            {
                "key": "subscription.manage",
                "label": "Change plan, cancel, resubscribe",
                "group": "Subscription",
            },
            {
                "key": "subscription.invoices.view",
                "label": "View own invoices",
                "group": "Subscription",
            },
            {
                "key": "subscription.tokens.view",
                "label": "View token balance",
                "group": "Subscription",
            },
            {
                "key": "subscription.tokens.manage",
                "label": "Purchase token bundles",
                "group": "Subscription",
            },
            {
                "key": "user.profile.view",
                "label": "View own profile",
                "group": "User",
            },
            {
                "key": "user.profile.manage",
                "label": "Edit profile",
                "group": "User",
            },
        ]

    def on_enable(self):
        import logging

        logger = logging.getLogger(__name__)

        from flask import current_app

        container = getattr(current_app, "container", None)
        if container:
            # Register the subscription-domain repository providers on the
            # shared DI container. Core declares none of these (they were
            # extracted to this plugin), so the plugin must add them — the
            # checkout/cancel handlers, line-item handlers, and other plugins
            # (e.g. yookassa) resolve them via container.<name>().
            from dependency_injector import providers
            from plugins.subscription.subscription.repositories.subscription_repository import (
                SubscriptionRepository,
            )
            from plugins.subscription.subscription.repositories.addon_subscription_repository import (
                AddOnSubscriptionRepository,
            )
            from plugins.subscription.subscription.repositories.addon_repository import (
                AddOnRepository,
            )
            from plugins.subscription.subscription.repositories.tarif_plan_repository import (
                TarifPlanRepository,
            )
            from plugins.subscription.subscription.repositories.tarif_plan_category_repository import (
                TarifPlanCategoryRepository,
            )

            container.subscription_repository = providers.Factory(
                SubscriptionRepository, session=container.db_session
            )
            container.addon_subscription_repository = providers.Factory(
                AddOnSubscriptionRepository, session=container.db_session
            )
            container.addon_repository = providers.Factory(
                AddOnRepository, session=container.db_session
            )
            container.tarif_plan_repository = providers.Factory(
                TarifPlanRepository, session=container.db_session
            )
            container.tarif_plan_category_repository = providers.Factory(
                TarifPlanCategoryRepository, session=container.db_session
            )
            logger.info(
                "[subscription] DI repository providers registered "
                "(subscription, addon, addon_subscription, tarif_plan, "
                "tarif_plan_category)"
            )

            dispatcher = container.event_dispatcher()

            from plugins.subscription.subscription.handlers.checkout_handler import (
                CheckoutHandler,
            )

            checkout_handler = CheckoutHandler(container)
            dispatcher.register("checkout.requested", checkout_handler)

            from plugins.subscription.subscription.handlers.cancel_handler import (
                SubscriptionCancelledHandler,
            )

            cancel_handler = SubscriptionCancelledHandler(container)
            dispatcher.register("subscription.cancelled", cancel_handler)

            logger.info(
                "[subscription] Domain event handlers registered "
                "(checkout.requested, subscription.cancelled)"
            )

        from vbwd.services.entitlement import register_entitlement_provider
        from plugins.subscription.subscription.services.subscription_entitlement_provider import (  # noqa: E501
            SubscriptionEntitlementProvider,
        )

        register_entitlement_provider(SubscriptionEntitlementProvider())
        logger.info("[subscription] Entitlement provider registered")

        from vbwd.services.subscription_read_model import (
            register_subscription_read_model,
        )
        from plugins.subscription.subscription.services.subscription_read_model import (  # noqa: E501
            SubscriptionReadModel,
        )

        register_subscription_read_model(SubscriptionReadModel())
        logger.info("[subscription] Subscription read model registered")

        from vbwd.services.demo_data_registry import (
            register_catalog_seeder,
            register_test_data_seeder,
            register_test_data_cleaner,
        )
        from plugins.subscription.subscription import demo_seed

        register_catalog_seeder(demo_seed.seed_catalog)
        register_test_data_seeder(demo_seed.seed_test_data)
        register_test_data_cleaner(demo_seed.clean_test_data)
        logger.info("[subscription] Demo/test data hooks registered")

        # Self-heal: ensure the /checkout/confirmation CMS page exists.
        # The fe-user `checkout` plugin's /checkout/confirmation route loads
        # CmsPage with slug="checkout-confirmation"; if the row is missing,
        # users see a 404 after paying. Subscription is enabled on every
        # instance, so seeding here guarantees the page is present after
        # any deploy — independent of whether `seed_data=true` was passed.
        # `populate_checkout_cms()` is idempotent (uses _get_or_create), so
        # this is safe to run on every boot.
        try:
            from plugins.checkout.populate_db import populate_checkout_cms

            populate_checkout_cms()
        except ImportError:
            logger.info(
                "[subscription] checkout plugin not installed — "
                "skipping checkout-confirmation page self-heal"
            )
        except (
            Exception
        ) as seed_error:  # noqa: BLE001 — never break boot for a seed failure
            logger.warning(
                "[subscription] Failed to self-heal checkout-confirmation page: %s",
                seed_error,
            )

        # Start scheduler
        try:
            from plugins.subscription.subscription.scheduler import (
                start_subscription_scheduler,
            )

            config = getattr(self, "config", {}) or {}
            interval = config.get("expiration_check_interval_seconds", 60)
            start_subscription_scheduler(current_app._get_current_object(), interval)
        except Exception as scheduler_error:
            logger.warning(
                "[subscription] Failed to start scheduler: %s", scheduler_error
            )

    def on_disable(self):
        from vbwd.services.entitlement import clear_entitlement_provider
        from vbwd.services.subscription_read_model import (
            clear_subscription_read_model,
        )
        from vbwd.services.demo_data_registry import clear_demo_data_hooks

        clear_entitlement_provider()
        clear_subscription_read_model()
        clear_demo_data_hooks()

    def register_event_handlers(self, event_bus):
        import logging

        logger = logging.getLogger(__name__)

        try:
            from plugins.subscription.subscription.handlers.subscription_handlers import (
                SubscriptionActivatedHandler,
            )

            activated_handler = SubscriptionActivatedHandler()
            event_bus.subscribe(
                "subscription.activated",
                lambda event_name, data: activated_handler.handle_activated(data),
            )
            logger.info("[subscription] EventBus handlers registered")
        except Exception as error:
            logger.warning(
                "[subscription] Failed to register event handlers: %s", error
            )

        # Register access level auto-assignment handler
        try:
            from plugins.subscription.subscription.handlers.access_level_handler import (
                SubscriptionAccessLevelHandler,
            )

            access_level_handler = SubscriptionAccessLevelHandler()
            event_bus.subscribe(
                "subscription.activated",
                access_level_handler.on_subscription_activated,
            )
            event_bus.subscribe(
                "subscription.cancelled",
                access_level_handler.on_subscription_cancelled,
            )
            logger.info("[subscription] Access level handlers registered")
        except Exception as error:
            logger.warning(
                "[subscription] Failed to register access level handlers: %s",
                error,
            )

    def register_line_item_handlers(self, registry):
        import logging

        logger = logging.getLogger(__name__)

        from flask import current_app

        container = getattr(current_app, "container", None)
        if not container:
            logger.warning(
                "[subscription] No container — cannot register line item handler"
            )
            return

        from plugins.subscription.subscription.handlers.line_item_handler import (
            SubscriptionLineItemHandler,
        )

        registry.register(SubscriptionLineItemHandler(container))
        logger.info("[subscription] SubscriptionLineItemHandler registered")

    def register_categories(self):
        return [
            {
                "name": "Subscription Plans",
                "slug": "subscription-plans",
                "description": "Default category for subscription plans",
                "is_single": True,
            },
        ]
