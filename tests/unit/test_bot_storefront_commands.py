"""Unit specs for the bot storefront COMMAND handlers (S53.0).

Provider-neutral: catalogs are MagicMock callables, the storefront service is a
MagicMock, and replies are built from the neutral ``BotReply`` / ``BotChoice``
DTOs. No DB, no transport.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from plugins.bot_base.bot_base.types import BotChoice, BotIdentity, BotReply
from plugins.subscription.subscription.services.bot_storefront_commands import (
    ACTION_SELECT_PLAN,
    ACTION_TOGGLE_ADDON,
    ACTION_TOGGLE_BUNDLE,
    BotStorefrontCommands,
    LINK_BALANCE_HINT,
    encode_action,
    parse_action,
)

PROVIDER = "telegram"
CHAT = "4242"
BASE_URL = "https://app.example.com"


def _catalog_entry(entry_id, name):
    return SimpleNamespace(id=entry_id, name=name)


def _reply_factory(*, text, choices):
    return BotReply(text=text, choices=choices)


def _choice_factory(*, label, action_data):
    return BotChoice(label=label, action_data=action_data)


def _commands(
    *,
    storefront_service=None,
    plans=(),
    addons=(),
    bundles=(),
    balance_reader=None,
):
    return BotStorefrontCommands(
        storefront_service=storefront_service or MagicMock(),
        active_plans=lambda: list(plans),
        active_addons=lambda: list(addons),
        active_token_bundles=lambda: list(bundles),
        checkout_link_base_url=BASE_URL,
        reply_factory=_reply_factory,
        choice_factory=_choice_factory,
        balance_reader=balance_reader,
    )


class TestActionEncoding:
    def test_round_trip(self):
        action_data = encode_action(ACTION_SELECT_PLAN, "plan-a")
        assert parse_action(action_data) == (ACTION_SELECT_PLAN, "plan-a")

    def test_foreign_namespace_is_rejected(self):
        assert parse_action("taro:draw:1") is None


class TestTarifsReply:
    def test_builds_choices_from_active_plans(self):
        commands = _commands(
            plans=[_catalog_entry("plan-a", "Basic"), _catalog_entry("plan-b", "Pro")]
        )
        reply = commands.tarifs_reply()
        assert [choice.label for choice in reply.choices] == ["Basic", "Pro"]
        assert reply.choices[0].action_data == encode_action(
            ACTION_SELECT_PLAN, "plan-a"
        )

    def test_tap_records_plan_replacing_prior(self):
        service = MagicMock()
        commands = _commands(storefront_service=service)
        commands.apply_action(
            provider_id=PROVIDER,
            chat_ref=CHAT,
            action_data=encode_action(ACTION_SELECT_PLAN, "plan-a"),
        )
        service.set_plan.assert_called_once_with(PROVIDER, CHAT, "plan-a")


class TestAddOnsReply:
    def test_builds_toggle_choices(self):
        commands = _commands(addons=[_catalog_entry("addon-x", "Extra storage")])
        reply = commands.add_ons_reply()
        assert reply.choices[0].action_data == encode_action(
            ACTION_TOGGLE_ADDON, "addon-x"
        )

    def test_tap_toggles_addon(self):
        service = MagicMock()
        commands = _commands(storefront_service=service)
        commands.apply_action(
            provider_id=PROVIDER,
            chat_ref=CHAT,
            action_data=encode_action(ACTION_TOGGLE_ADDON, "addon-x"),
        )
        service.toggle_addon.assert_called_once_with(PROVIDER, CHAT, "addon-x")


class TestTokensReply:
    def _linked_identity(self):
        from uuid import uuid4

        return BotIdentity(
            provider_id=PROVIDER, external_user_id="7", vbwd_user_id=uuid4()
        )

    def test_catalog_choices_are_anonymous(self):
        commands = _commands(bundles=[_catalog_entry("bundle-1", "100 tokens")])
        reply = commands.tokens_reply(identity=None)
        assert reply.choices[0].action_data == encode_action(
            ACTION_TOGGLE_BUNDLE, "bundle-1"
        )

    def test_unlinked_shows_hint_and_no_balance(self):
        commands = _commands(
            bundles=[_catalog_entry("bundle-1", "100 tokens")],
            balance_reader=lambda _identity: 999,  # must NOT be consulted
        )
        reply = commands.tokens_reply(identity=None)
        assert LINK_BALANCE_HINT in reply.text
        assert "999" not in reply.text

    def test_linked_shows_balance_line(self):
        identity = self._linked_identity()
        reads = []

        def balance_reader(passed_identity):
            reads.append(passed_identity)
            return 250

        commands = _commands(
            bundles=[_catalog_entry("bundle-1", "100 tokens")],
            balance_reader=balance_reader,
        )
        reply = commands.tokens_reply(identity=identity)
        assert "You have 250 tokens" in reply.text
        assert reads == [identity]

    def test_tap_toggles_bundle(self):
        service = MagicMock()
        commands = _commands(storefront_service=service)
        commands.apply_action(
            provider_id=PROVIDER,
            chat_ref=CHAT,
            action_data=encode_action(ACTION_TOGGLE_BUNDLE, "bundle-1"),
        )
        service.toggle_token_bundle.assert_called_once_with(PROVIDER, CHAT, "bundle-1")


class TestCheckoutReply:
    def test_mints_token_and_returns_draft_link(self):
        service = MagicMock()
        service.mint_checkout_token.return_value = "tok-123"
        commands = _commands(storefront_service=service)

        reply = commands.checkout_reply(provider_id=PROVIDER, chat_ref=CHAT)

        service.mint_checkout_token.assert_called_once_with(PROVIDER, CHAT)
        assert f"{BASE_URL}/checkout?draft=tok-123" in reply.text

    def test_empty_cart_returns_hint_not_link(self):
        service = MagicMock()
        service.mint_checkout_token.return_value = None
        commands = _commands(storefront_service=service)

        reply = commands.checkout_reply(provider_id=PROVIDER, chat_ref=CHAT)

        assert "draft=" not in reply.text
