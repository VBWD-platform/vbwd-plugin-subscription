"""Unit specs for the subscription bot-storefront CONSUMER seam (S53.0).

``subscription`` implements ``BotCommandProvider`` (bot_namespace="subscription")
so its storefront commands light up over every bot adapter unchanged. The bridge
is **optional**: the bot methods lazily import bot-base's neutral DTOs inside the
method body, so ``subscription`` imports cleanly even when bot-base is absent.

These specs drive ``handle_action`` with a fake (MagicMock) storefront command
handler and a fake non-Telegram provider — no DB, no transport — proving the
storefront is provider-neutral.
"""
import sys
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from plugins.bot_base.bot_base.ports import BotCommandProvider
from plugins.bot_base.bot_base.services.command_registry import CommandRegistry
from plugins.bot_base.bot_base.types import BotIdentity, BotInbound, BotReply, ChatRef
from plugins.bot_base.tests.unit.fakes import FakePluginManager
from plugins.subscription import SubscriptionPlugin
from plugins.subscription.subscription.services.bot_storefront_commands import (
    ACTION_SELECT_PLAN,
    ADD_ONS_COMMAND,
    CHECKOUT_COMMAND,
    TARIFS_COMMAND,
    TOKENS_COMMAND,
    encode_action,
)


def _inbound(*, provider_id="telegram", command=None, action_data=None, identity=None):
    chat_ref = ChatRef(provider_id=provider_id, chat_id="4242")
    return BotInbound(
        provider_id=provider_id,
        chat_ref=chat_ref,
        sender_ref="7",
        command=command,
        action_data=action_data,
        identity=identity,
    )


def _linked_identity():
    return BotIdentity(
        provider_id="telegram", external_user_id="7", vbwd_user_id=uuid4()
    )


@pytest.fixture
def enabled_plugin():
    plugin = SubscriptionPlugin()
    plugin.initialize({"bot_storefront_enabled": True})
    return plugin


class TestCommandRegistryCollection:
    def test_plugin_structurally_implements_provider_seam(self, enabled_plugin):
        assert isinstance(enabled_plugin, BotCommandProvider)

    def test_registry_collects_storefront_commands_when_enabled(self, enabled_plugin):
        registry = CommandRegistry(FakePluginManager([enabled_plugin]))
        index = registry.command_index()
        for name in (
            TARIFS_COMMAND,
            ADD_ONS_COMMAND,
            TOKENS_COMMAND,
            CHECKOUT_COMMAND,
        ):
            assert index[name] is enabled_plugin

    def test_no_commands_when_storefront_disabled(self):
        plugin = SubscriptionPlugin()
        plugin.initialize({"bot_storefront_enabled": False})
        registry = CommandRegistry(FakePluginManager([plugin]))
        assert registry.command_index() == {}
        assert plugin.get_bot_commands() == []

    def test_storefront_disabled_is_the_default(self):
        plugin = SubscriptionPlugin()
        plugin.initialize({})
        assert plugin.get_bot_commands() == []


class TestHandleActionDispatch:
    """``handle_action`` routes each command/tap to the storefront handler.

    A fake ``BotStorefrontCommands`` (MagicMock) is injected via
    ``_build_storefront_commands`` so the dispatch wiring is tested without a DB.
    """

    def _with_fake_handler(self, plugin, monkeypatch):
        fake_handler = MagicMock()
        fake_handler.tarifs_reply.return_value = BotReply(text="tarifs")
        fake_handler.add_ons_reply.return_value = BotReply(text="add-ons")
        fake_handler.tokens_reply.return_value = BotReply(text="tokens")
        fake_handler.checkout_reply.return_value = BotReply(text="checkout")
        fake_handler.apply_action.return_value = BotReply(text="applied")
        monkeypatch.setattr(plugin, "_build_storefront_commands", lambda: fake_handler)
        return fake_handler

    def test_tarifs_command(self, enabled_plugin, monkeypatch):
        handler = self._with_fake_handler(enabled_plugin, monkeypatch)
        reply = enabled_plugin.handle_action(_inbound(command=TARIFS_COMMAND))
        handler.tarifs_reply.assert_called_once()
        assert reply.text == "tarifs"

    def test_add_ons_command(self, enabled_plugin, monkeypatch):
        handler = self._with_fake_handler(enabled_plugin, monkeypatch)
        enabled_plugin.handle_action(_inbound(command=ADD_ONS_COMMAND))
        handler.add_ons_reply.assert_called_once()

    def test_tokens_command_passes_identity(self, enabled_plugin, monkeypatch):
        handler = self._with_fake_handler(enabled_plugin, monkeypatch)
        identity = _linked_identity()
        enabled_plugin.handle_action(
            _inbound(command=TOKENS_COMMAND, identity=identity)
        )
        handler.tokens_reply.assert_called_once_with(identity=identity)

    def test_checkout_command_passes_chat(self, enabled_plugin, monkeypatch):
        handler = self._with_fake_handler(enabled_plugin, monkeypatch)
        enabled_plugin.handle_action(_inbound(command=CHECKOUT_COMMAND))
        handler.checkout_reply.assert_called_once_with(
            provider_id="telegram", chat_ref="4242"
        )

    def test_tapped_choice_routes_to_apply_action(self, enabled_plugin, monkeypatch):
        handler = self._with_fake_handler(enabled_plugin, monkeypatch)
        action_data = encode_action(ACTION_SELECT_PLAN, "plan-a")
        enabled_plugin.handle_action(_inbound(action_data=action_data))
        handler.apply_action.assert_called_once_with(
            provider_id="telegram", chat_ref="4242", action_data=action_data
        )


class TestProviderNeutrality:
    """A fake non-Telegram provider drives the same handlers unchanged (D6/D7)."""

    def test_non_telegram_provider_routes_identically(
        self, enabled_plugin, monkeypatch
    ):
        captured = {}

        def fake_handler_factory():
            handler = MagicMock()
            handler.checkout_reply.side_effect = (
                lambda *, provider_id, chat_ref: captured.update(
                    provider_id=provider_id, chat_ref=chat_ref
                )
                or BotReply(text="checkout")
            )
            return handler

        monkeypatch.setattr(
            enabled_plugin, "_build_storefront_commands", fake_handler_factory
        )

        enabled_plugin.handle_action(
            _inbound(provider_id="meinchat", command=CHECKOUT_COMMAND)
        )

        assert captured == {"provider_id": "meinchat", "chat_ref": "4242"}


class TestBridgeOptional:
    def test_subscription_imports_without_bot_base_on_path(self):
        """The subscription package must import even when bot_base is absent."""
        import importlib

        blocked_prefixes = ("plugins.bot_base", "plugins.subscription")
        saved = {
            name: module
            for name, module in sys.modules.items()
            if name.startswith(blocked_prefixes)
        }
        for name in list(saved):
            del sys.modules[name]

        class _BlockBotBase:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "plugins.bot_base" or fullname.startswith(
                    "plugins.bot_base."
                ):
                    raise ImportError("bot_base is absent in this scenario")
                return None

        finder = _BlockBotBase()
        sys.meta_path.insert(0, finder)
        try:
            module = importlib.import_module("plugins.subscription")
            plugin = module.SubscriptionPlugin()
            assert plugin.metadata.name == "subscription"
            assert "bot-base" not in (plugin.metadata.dependencies or [])
            assert "bot_base" not in (plugin.metadata.dependencies or [])
        finally:
            sys.meta_path.remove(finder)
            for name in list(sys.modules):
                if name.startswith(blocked_prefixes):
                    del sys.modules[name]
            sys.modules.update(saved)

    def test_bot_base_not_a_hard_dependency(self, enabled_plugin):
        deps = enabled_plugin.metadata.dependencies or []
        assert "bot-base" not in deps
        assert "bot_base" not in deps


def test_unrecognized_input_returns_guidance(enabled_plugin, monkeypatch):
    monkeypatch.setattr(
        enabled_plugin, "_build_storefront_commands", lambda: MagicMock()
    )
    reply = enabled_plugin.handle_action(_inbound())
    assert isinstance(reply, BotReply)
    assert "/tarifs" in reply.text
