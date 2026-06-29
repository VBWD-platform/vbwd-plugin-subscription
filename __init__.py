"""Subscription plugin — plans, subscriptions, add-ons, categories, checkout."""
from typing import List, Optional, TYPE_CHECKING

from vbwd.plugins.base import BasePlugin, PluginMetadata

if TYPE_CHECKING:
    # Bot-base is an OPTIONAL bridge (S45 / S53.0 / D1 inversion). It is imported
    # only for type checking here; at runtime the bot-storefront methods lazily
    # import the neutral DTOs inside their bodies, so ``subscription`` imports
    # cleanly even when bot-base is absent (no hard dependency, no top-level
    # ``bot_base`` import). Mirrors plugins/chat and plugins/tarot exactly.
    from plugins.bot_base.bot_base.types import BotCommand, BotInbound, BotReply


DEFAULT_CONFIG = {
    "trial_days": 14,
    "dunning_intervals_days": [3, 7],
    "expiration_check_interval_seconds": 60,
    "max_subscriptions_per_user": 10,
    "allow_downgrade": True,
    "proration_enabled": True,
    # ── bot commerce storefront (S53.0) ──────────────────────────────────────
    # When false (default) get_bot_commands() returns [] so bot-base never
    # surfaces the storefront commands — web behaviour is entirely unchanged.
    "bot_storefront_enabled": False,
    # The public fe-user origin the /checkout draft link points at.
    "checkout_link_base_url": "",
    # How long a minted one-time checkout-draft token stays resolvable.
    "checkout_draft_ttl_seconds": 900,
}


BOT_NAMESPACE = "subscription"


# Frontend event types this plugin contributes to the core security whitelist
# (POST /api/v1/events). Core owns only generic platform types; the subscription
# domain types live here so core names no plugin domain.
SUBSCRIPTION_FRONTEND_EVENT_TYPES = {
    "subscription:created",
    "subscription:activated",
    "subscription:upgraded",
    "subscription:downgraded",
    "subscription:cancelled",
    "subscription:expired",
}


def register_subscription_frontend_event_types() -> None:
    """Add the subscription frontend event types to the core whitelist."""
    from vbwd.services.frontend_event_type_registry import (
        register_frontend_event_types,
    )

    register_frontend_event_types(SUBSCRIPTION_FRONTEND_EVENT_TYPES)


def unregister_subscription_frontend_event_types() -> None:
    """Remove the subscription frontend event types (plugin disable)."""
    from vbwd.services.frontend_event_type_registry import (
        unregister_frontend_event_types,
    )

    unregister_frontend_event_types(SUBSCRIPTION_FRONTEND_EVENT_TYPES)


class SubscriptionPlugin(BasePlugin):
    #: The owning namespace bot-base routes commands / taps to (D1/D7). A class
    #: attribute so the plugin structurally implements ``BotCommandProvider``.
    bot_namespace = BOT_NAMESPACE

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="subscription",
            version="26.6",
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

    def _register_data_exchangers(self) -> None:
        """Register the subscription entity exchangers into the data-exchange seam.

        Core declares none of these (it stays agnostic); the plugin adds them on
        enable through the shared ``db.session`` so subscription plans, add-ons
        and (export-only) subscription records appear on the generic Settings →
        Import/Export page. Clear-safe: re-registering replaces by key (per-test
        app re-enable).
        """
        import logging

        try:
            from vbwd.extensions import db
            from plugins.subscription.subscription.services.data_exchange.subscription_exchangers import (  # noqa: E501
                register_subscription_exchangers,
            )

            register_subscription_exchangers(db.session)
        except Exception as exchanger_error:
            logging.getLogger(__name__).warning(
                "[subscription] Failed to register data exchangers: %s",
                exchanger_error,
            )

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

        from vbwd.services.invoice_extra_fields_registry import (
            register_invoice_extra_fields_provider,
        )
        from plugins.subscription.subscription.services.subscription_read_model import (  # noqa: E501
            SubscriptionReadModel,
        )

        register_invoice_extra_fields_provider(
            "subscription",
            lambda invoice: SubscriptionReadModel().enrich_invoice(invoice),
        )
        logger.info("[subscription] Invoice extra-fields provider registered")

        from vbwd.services.deletion_dependency_registry import (
            register_deletion_dependency_provider,
        )

        def _subscription_deletion_dependency(user_id):
            count = SubscriptionReadModel().count_user_subscriptions(user_id)
            if count > 0:
                return {
                    "type": "subscription",
                    "count": count,
                    "label": "Subscriptions",
                }
            return None

        register_deletion_dependency_provider(
            "subscription", _subscription_deletion_dependency
        )
        logger.info("[subscription] Deletion-dependency provider registered")

        register_subscription_frontend_event_types()
        logger.info("[subscription] Frontend event types registered")

        self._register_data_exchangers()

        # S77 — make plans and add-ons addressable by the generic tags /
        # custom-fields framework. Registering these entity types lets the core
        # value endpoints (GET|PUT /admin/<type>/<id>/{tags,custom-fields})
        # return 200 (each gated by its own manage permission) instead of 404.
        from vbwd.services.entity_type_registry import (
            EntityTypeRegistration,
            register_entity_type,
        )

        register_entity_type(
            EntityTypeRegistration(
                "tarif_plan", "Tarif plan", "subscription.plans.manage"
            )
        )
        register_entity_type(
            EntityTypeRegistration("addon", "Add-on", "subscription.addons.manage")
        )
        logger.info("[subscription] Entity types registered (tarif_plan, addon)")

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

        # Cross-entity search seam — contribute active subscription plans to the
        # agnostic search registry so the /search bot can find them (idempotent:
        # register replaces by entity_type). Core names no plan vocabulary.
        from vbwd.services.search import search_provider_registry
        from plugins.subscription.subscription.search_provider import (
            SubscriptionPlanSearchProvider,
        )

        search_provider_registry.register(SubscriptionPlanSearchProvider())

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

        # Register the `flask subscription ...` CLI group (cron entrypoint for
        # the same billing pass the scheduler runs). Runs under TESTING too so
        # CLI specs can drive the command via the app's test runner.
        self._register_cli_commands()

        # Start scheduler — but never in tests. Each test builds its own app and
        # runs on_enable, so an unguarded scheduler spins up one background
        # thread (and its DB work) per test app, leaking threads/connections
        # across a full-suite run. Core guards its booking scheduler the same
        # way (see vbwd/app.py).
        if not current_app.config.get("TESTING"):
            try:
                from plugins.subscription.subscription.scheduler import (
                    start_subscription_scheduler,
                )

                config = getattr(self, "config", {}) or {}
                interval = config.get("expiration_check_interval_seconds", 60)
                start_subscription_scheduler(
                    current_app._get_current_object(), interval
                )
            except Exception as scheduler_error:
                logger.warning(
                    "[subscription] Failed to start scheduler: %s", scheduler_error
                )

    def _register_cli_commands(self) -> None:
        """Register the plugin's ``flask subscription ...`` CLI group.

        Core declares no subscription command (it stays agnostic); the plugin
        adds its group to the live app's click group on enable. Guarded so a
        repeat enable (e.g. per-test app) does not raise on a duplicate name.
        """
        import logging
        from flask import current_app

        try:
            from plugins.subscription.subscription.cli import subscription_cli

            if "subscription" not in current_app.cli.commands:
                current_app.cli.add_command(subscription_cli)
        except Exception as cli_error:  # pragma: no cover — operational guard
            logging.getLogger(__name__).warning(
                "[subscription] Failed to register CLI commands: %s", cli_error
            )

    def on_disable(self):
        from vbwd.services.entitlement import clear_entitlement_provider
        from vbwd.services.invoice_extra_fields_registry import (
            unregister_invoice_extra_fields_provider,
        )
        from vbwd.services.demo_data_registry import clear_demo_data_hooks
        from vbwd.services.deletion_dependency_registry import (
            unregister_deletion_dependency_provider,
        )

        from vbwd.services.entity_type_registry import unregister_entity_type

        unregister_entity_type("tarif_plan")
        unregister_entity_type("addon")

        from vbwd.services.search import search_provider_registry

        search_provider_registry.unregister("subscription_plan")

        clear_entitlement_provider()
        unregister_invoice_extra_fields_provider("subscription")
        clear_demo_data_hooks()
        unregister_deletion_dependency_provider("subscription")
        unregister_subscription_frontend_event_types()

    def register_event_handlers(self, event_bus):
        import logging

        logger = logging.getLogger(__name__)

        # S50.4 — subscribe to the domain-neutral recurring-billing facts that
        # payment plugins publish (link/renew/cancel/fail). Replaces the former
        # core ISubscriptionLifecycle port; payment plugins stay subscription-free
        # (no subscriber ⇒ published fact is a no-op).
        try:
            from plugins.subscription.subscription.handlers.recurring_billing_subscriber import (  # noqa: E501
                RecurringBillingSubscriber,
            )

            RecurringBillingSubscriber().subscribe(event_bus)
            logger.info("[subscription] Recurring-billing subscribers registered")
        except Exception as error:
            logger.warning(
                "[subscription] Failed to register recurring-billing subscribers: %s",
                error,
            )

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

        # S69 — plan/add-on driven permission reconciliation. Every lifecycle
        # fact (activate/cancel/expire for subscriptions and add-ons) triggers a
        # full reconcile of the user's permissions from their active sources.
        try:
            from plugins.subscription.subscription.handlers.permission_sync_handler import (  # noqa: E501
                PermissionSyncHandler,
            )

            permission_sync_handler = PermissionSyncHandler()
            for event_name in (
                "subscription.activated",
                "subscription.cancelled",
                "subscription.expired",
                "addon.activated",
                "addon.cancelled",
            ):
                event_bus.subscribe(
                    event_name, permission_sync_handler.on_lifecycle_event
                )
            logger.info("[subscription] Permission-sync handlers registered")
        except Exception as error:
            logger.warning(
                "[subscription] Failed to register permission-sync handlers: %s",
                error,
            )

        # S73 — plan/add-on driven user-group reconciliation. Every lifecycle
        # fact triggers a full reconcile of the user's MANAGED group
        # memberships from their active sources' check-in/check-out config.
        try:
            from plugins.subscription.subscription.handlers.group_sync_handler import (  # noqa: E501
                GroupSyncHandler,
            )

            group_sync_handler = GroupSyncHandler()
            for event_name in (
                "subscription.activated",
                "subscription.cancelled",
                "subscription.expired",
                "addon.activated",
                "addon.cancelled",
            ):
                event_bus.subscribe(event_name, group_sync_handler.on_lifecycle_event)
            logger.info("[subscription] Group-sync handlers registered")
        except Exception as error:
            logger.warning(
                "[subscription] Failed to register group-sync handlers: %s",
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

    # ── bot-base consumer seam: commerce storefront (S53.0) ───────────────────
    def get_bot_commands(self) -> List["BotCommand"]:
        """The storefront commands ``subscription`` contributes to the bot menu.

        Returns ``[]`` when ``bot_storefront_enabled`` is false so bot-base's
        ``CommandRegistry`` never surfaces ``/tarifs`` / ``/add-ons`` /
        ``/tokens`` / ``/checkout`` — the subscription web app stays entirely
        untouched. The neutral ``BotCommand`` DTO is imported lazily so this
        module loads even when bot-base is absent (the bridge is optional).
        """
        if not self.get_config("bot_storefront_enabled", False):
            return []

        from plugins.bot_base.bot_base.types import BotCommand
        from plugins.subscription.subscription.services.bot_storefront_commands import (
            ADD_ONS_COMMAND,
            CART_CLEAR_COMMAND,
            CART_COMMAND,
            CART_EDIT_COMMAND,
            CHECKOUT_COMMAND,
            TARIFS_COMMAND,
            TOKENS_COMMAND,
        )

        def command(name: str, description: str) -> "BotCommand":
            return BotCommand(
                name=name, description=description, namespace=BOT_NAMESPACE
            )

        return [
            command(TARIFS_COMMAND, "Browse tarif plans"),
            command(ADD_ONS_COMMAND, "Browse add-ons"),
            command(TOKENS_COMMAND, "Browse token bundles (and your balance)"),
            command(CART_COMMAND, "Show your cart"),
            command(CART_EDIT_COMMAND, "Edit your cart (remove items)"),
            command(CART_CLEAR_COMMAND, "Empty your cart"),
            command(CHECKOUT_COMMAND, "Get a link to complete your purchase"),
        ]

    def handle_action(self, context: "BotInbound") -> "BotReply":
        """Handle a storefront command or a tapped choice routed to ``subscription``.

        Every storefront path is ANONYMOUS — no billing, no identity mutation.
        The only identity-aware branch is the ``/tokens`` balance line, which is
        shown when the chat is linked and silently omitted otherwise (D3).
        """
        from plugins.subscription.subscription.services.bot_storefront_commands import (
            ADD_ONS_COMMAND,
            CART_CLEAR_COMMAND,
            CART_COMMAND,
            CART_EDIT_COMMAND,
            CHECKOUT_COMMAND,
            TARIFS_COMMAND,
            TOKENS_COMMAND,
        )

        commands = self._build_storefront_commands()
        provider_id = context.chat_ref.provider_id
        chat_ref = context.chat_ref.chat_id

        if context.command == TARIFS_COMMAND:
            return commands.tarifs_reply()
        if context.command == ADD_ONS_COMMAND:
            return commands.add_ons_reply()
        if context.command == TOKENS_COMMAND:
            return commands.tokens_reply(identity=context.identity)
        if context.command == CART_COMMAND:
            return commands.cart_reply(provider_id=provider_id, chat_ref=chat_ref)
        if context.command == CART_CLEAR_COMMAND:
            return commands.cart_clear_reply(provider_id=provider_id, chat_ref=chat_ref)
        if context.command == CART_EDIT_COMMAND:
            return commands.cart_edit_reply(provider_id=provider_id, chat_ref=chat_ref)
        if context.command == CHECKOUT_COMMAND:
            return commands.checkout_reply(provider_id=provider_id, chat_ref=chat_ref)

        if context.action_data:
            return commands.apply_action(
                provider_id=provider_id,
                chat_ref=chat_ref,
                action_data=context.action_data,
            )

        from plugins.bot_base.bot_base.types import BotReply

        return BotReply(
            text="Send /tarifs, /add-ons, /tokens, /cart, or /checkout to shop.",
            choices=[],
        )

    def _build_storefront_commands(self):
        """Build the storefront command handler wired to live catalogs (DRY).

        Resolves plan/add-on/token-bundle catalogs and the draft service off the
        live ``db.session`` exactly as the web routes do, and reads the token
        balance from **core** (``TokenBalanceRepository`` — a plugin→core read,
        allowed). The neutral ``BotReply`` / ``BotChoice`` constructors are passed
        in as factories so this builder never hard-imports bot-base at module
        load.
        """
        from flask import current_app

        from plugins.bot_base.bot_base.types import BotChoice, BotReply
        from plugins.subscription.subscription.services.bot_storefront_commands import (
            BotStorefrontCommands,
        )

        config = current_app.config_store.get_config("subscription")
        ttl_seconds = config.get(
            "checkout_draft_ttl_seconds",
            DEFAULT_CONFIG["checkout_draft_ttl_seconds"],
        )
        base_url = config.get("checkout_link_base_url", "")

        storefront_service = self._build_storefront_service(ttl_seconds)

        return BotStorefrontCommands(
            storefront_service=storefront_service,
            active_plans=self._active_plans,
            active_addons=self._active_addons,
            active_token_bundles=self._active_token_bundles,
            checkout_link_base_url=base_url,
            reply_factory=lambda *, text, choices, meta=None: BotReply(
                text=text, choices=choices, meta=meta
            ),
            choice_factory=lambda *, label, action_data, hint=None: BotChoice(
                label=label, action_data=action_data, hint=hint
            ),
            balance_reader=self._read_token_balance,
        )

    def _build_storefront_service(self, ttl_seconds: int):
        from vbwd.extensions import db
        from plugins.subscription.subscription.repositories.bot_checkout_draft_repository import (  # noqa: E501
            BotCheckoutDraftRepository,
        )
        from plugins.subscription.subscription.services.bot_storefront_service import (
            BotStorefrontService,
        )

        return BotStorefrontService(
            BotCheckoutDraftRepository(db.session),
            checkout_draft_ttl_seconds=ttl_seconds,
        )

    def _active_plans(self):
        from vbwd.extensions import db
        from plugins.subscription.subscription.repositories.tarif_plan_repository import (  # noqa: E501
            TarifPlanRepository,
        )

        return TarifPlanRepository(db.session).find_active()

    def _active_addons(self):
        from vbwd.extensions import db
        from plugins.subscription.subscription.repositories.addon_repository import (
            AddOnRepository,
        )

        return AddOnRepository(db.session).find_active()

    def _active_token_bundles(self):
        from vbwd.extensions import db
        from vbwd.repositories.token_bundle_repository import TokenBundleRepository

        return TokenBundleRepository(db.session).find_active()

    def _read_token_balance(self, identity) -> Optional[int]:
        """Read the core token balance for a linked chat (plugin→core read)."""
        from vbwd.extensions import db
        from vbwd.repositories.token_repository import TokenBalanceRepository

        balance = TokenBalanceRepository(db.session).find_by_user_id(
            identity.vbwd_user_id
        )
        return balance.balance if balance is not None else 0
