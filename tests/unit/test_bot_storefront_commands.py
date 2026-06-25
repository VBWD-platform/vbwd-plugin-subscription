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


def _catalog_entry(entry_id, name, price=None, currency="EUR", billing_period=None):
    return SimpleNamespace(
        id=entry_id,
        name=name,
        price=price,
        currency=currency,
        billing_period=billing_period,
    )


def _reply_factory(*, text, choices, meta=None):
    return BotReply(text=text, choices=choices, meta=meta)


def _choice_factory(*, label, action_data, hint=None):
    return BotChoice(label=label, action_data=action_data, hint=hint)


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
        assert parse_action("tarot:draw:1") is None


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


class TestChoiceHintsAndCleanPrompt:
    """S70.3 — choice replies carry price hints + a clean ``meta.text`` prompt
    while keeping the numbered body as the non-rich fallback."""

    def test_tarifs_choices_carry_monthly_price_hint(self):
        from vbwd.models.enums import BillingPeriod

        commands = _commands(
            plans=[
                _catalog_entry(
                    "plan-a",
                    "Pro",
                    price="29.00",
                    currency="EUR",
                    billing_period=BillingPeriod.MONTHLY.value,
                )
            ]
        )
        reply = commands.tarifs_reply()
        assert reply.choices[0].hint == "€29/mo"

    def test_tarifs_meta_is_bot_choices_with_clean_prompt(self):
        commands = _commands(plans=[_catalog_entry("plan-a", "Pro")])
        reply = commands.tarifs_reply()
        assert reply.meta["kind"] == "bot_choices"
        assert reply.meta["text"] == reply.text
        assert "1." not in reply.meta["text"]

    def test_tokens_bundle_hint_is_a_plain_price(self):
        commands = _commands(
            bundles=[_catalog_entry("bundle-1", "1k", price="20.00", currency=None)]
        )
        reply = commands.tokens_reply(identity=None)
        assert reply.choices[0].hint == "€20"


class TestCartReply:
    def test_cart_builds_bot_cart_from_compute_cart(self):
        service = MagicMock()
        service.compute_cart.return_value = {
            "items": [
                {
                    "item_type": "SUBSCRIPTION",
                    "item_id": "plan-a",
                    "name": "Pro",
                    "quantity": 1,
                    "unit_price": "29.00",
                    "line_total": "29.00",
                }
            ],
            "total": "29.00",
            "currency": "EUR",
        }
        commands = _commands(storefront_service=service)

        reply = commands.cart_reply(provider_id=PROVIDER, chat_ref=CHAT)

        assert reply.meta["kind"] == "bot_cart"
        assert reply.meta["total"] == "29.00"
        assert reply.meta["currency"] == "EUR"
        assert reply.meta["items"][0]["name"] == "Pro"
        # The bot_cart payload never leaks the internal item_type/item_id.
        assert "item_type" not in reply.meta["items"][0]
        assert "Pro" in reply.text

    def test_empty_cart_is_a_friendly_empty_bot_cart(self):
        service = MagicMock()
        service.compute_cart.return_value = {
            "items": [],
            "total": "0",
            "currency": "EUR",
        }
        commands = _commands(storefront_service=service)

        reply = commands.cart_reply(provider_id=PROVIDER, chat_ref=CHAT)

        assert reply.meta["kind"] == "bot_cart"
        assert reply.meta["items"] == []


class TestCartClear:
    def test_cart_clear_empties_draft_and_returns_empty_cart(self):
        service = MagicMock()
        service.compute_cart.return_value = {
            "items": [],
            "total": "0",
            "currency": "EUR",
        }
        commands = _commands(storefront_service=service)

        reply = commands.cart_clear_reply(provider_id=PROVIDER, chat_ref=CHAT)

        service.clear_draft.assert_called_once_with(PROVIDER, CHAT)
        assert reply.meta["kind"] == "bot_cart"
        assert reply.meta["items"] == []


class TestCartEdit:
    def _draft_cart(self):
        return {
            "items": [
                {
                    "item_type": "SUBSCRIPTION",
                    "item_id": "plan-a",
                    "name": "Pro",
                    "quantity": 1,
                    "unit_price": "29.00",
                    "line_total": "29.00",
                },
                {
                    "item_type": "ADD_ON",
                    "item_id": "addon-x",
                    "name": "Extra",
                    "quantity": 1,
                    "unit_price": "9.00",
                    "line_total": "9.00",
                },
            ],
            "total": "38.00",
            "currency": "EUR",
        }

    def test_cart_edit_lists_one_remove_choice_per_line(self):
        service = MagicMock()
        service.compute_cart.return_value = self._draft_cart()
        commands = _commands(storefront_service=service)

        reply = commands.cart_edit_reply(provider_id=PROVIDER, chat_ref=CHAT)

        assert reply.meta == {
            "kind": "bot_choices",
            "text": "Tap any item to remove it",
        }
        assert len(reply.choices) == 2
        first = reply.choices[0]
        assert first.action_data == "subscription:remove:SUBSCRIPTION:plan-a"
        assert "Pro" in first.label and "×1" in first.label

    def test_cart_edit_empty_draft_is_empty_cart_state(self):
        service = MagicMock()
        service.compute_cart.return_value = {
            "items": [],
            "total": "0",
            "currency": "EUR",
        }
        commands = _commands(storefront_service=service)

        reply = commands.cart_edit_reply(provider_id=PROVIDER, chat_ref=CHAT)

        assert reply.meta["kind"] == "bot_cart"
        assert reply.choices == []


class TestAddPathRepliesWithCart:
    """S70.x — every add/toggle tap returns the running ``bot_cart`` card (line
    items + total + checkout button via the fe) so the user can check out
    immediately, instead of the terse text confirmation."""

    def test_select_plan_returns_bot_cart_with_the_added_item(self):
        service = MagicMock()
        service.compute_cart.return_value = {
            "items": [
                {
                    "item_type": "SUBSCRIPTION",
                    "item_id": "plan-a",
                    "name": "Pro",
                    "quantity": 1,
                    "unit_price": "29.00",
                    "line_total": "29.00",
                }
            ],
            "total": "29.00",
            "currency": "EUR",
        }
        commands = _commands(storefront_service=service)

        reply = commands.apply_action(
            provider_id=PROVIDER,
            chat_ref=CHAT,
            action_data=encode_action(ACTION_SELECT_PLAN, "plan-a"),
        )

        service.set_plan.assert_called_once_with(PROVIDER, CHAT, "plan-a")
        assert reply.meta["kind"] == "bot_cart"
        assert reply.meta["items"][0]["name"] == "Pro"
        assert reply.meta["total"] == "29.00"

    def test_toggle_addon_returns_bot_cart_listing_both_items_with_total(self):
        service = MagicMock()
        service.compute_cart.return_value = {
            "items": [
                {
                    "item_type": "SUBSCRIPTION",
                    "item_id": "plan-a",
                    "name": "Pro",
                    "quantity": 1,
                    "unit_price": "29.00",
                    "line_total": "29.00",
                },
                {
                    "item_type": "ADD_ON",
                    "item_id": "addon-x",
                    "name": "Extra",
                    "quantity": 1,
                    "unit_price": "9.00",
                    "line_total": "9.00",
                },
            ],
            "total": "38.00",
            "currency": "EUR",
        }
        commands = _commands(storefront_service=service)

        reply = commands.apply_action(
            provider_id=PROVIDER,
            chat_ref=CHAT,
            action_data=encode_action(ACTION_TOGGLE_ADDON, "addon-x"),
        )

        service.toggle_addon.assert_called_once_with(PROVIDER, CHAT, "addon-x")
        assert reply.meta["kind"] == "bot_cart"
        assert [item["name"] for item in reply.meta["items"]] == ["Pro", "Extra"]
        assert reply.meta["total"] == "38.00"

    def test_toggle_bundle_returns_bot_cart(self):
        service = MagicMock()
        service.compute_cart.return_value = {
            "items": [
                {
                    "item_type": "TOKEN_BUNDLE",
                    "item_id": "bundle-1",
                    "name": "100 tokens",
                    "quantity": 1,
                    "unit_price": "20.00",
                    "line_total": "20.00",
                }
            ],
            "total": "20.00",
            "currency": "EUR",
        }
        commands = _commands(storefront_service=service)

        reply = commands.apply_action(
            provider_id=PROVIDER,
            chat_ref=CHAT,
            action_data=encode_action(ACTION_TOGGLE_BUNDLE, "bundle-1"),
        )

        service.toggle_token_bundle.assert_called_once_with(PROVIDER, CHAT, "bundle-1")
        assert reply.meta["kind"] == "bot_cart"
        assert reply.meta["items"][0]["name"] == "100 tokens"

    def test_toggle_off_that_empties_the_cart_shows_empty_bot_cart(self):
        service = MagicMock()
        service.compute_cart.return_value = {
            "items": [],
            "total": "0",
            "currency": "EUR",
        }
        commands = _commands(storefront_service=service)

        reply = commands.apply_action(
            provider_id=PROVIDER,
            chat_ref=CHAT,
            action_data=encode_action(ACTION_TOGGLE_ADDON, "addon-x"),
        )

        service.toggle_addon.assert_called_once_with(PROVIDER, CHAT, "addon-x")
        assert reply.meta["kind"] == "bot_cart"
        assert reply.meta["items"] == []


class TestRemoveAction:
    def test_remove_action_removes_line_and_replies_with_edit_list(self):
        service = MagicMock()
        # After removal the remaining line is the add-on.
        service.compute_cart.return_value = {
            "items": [
                {
                    "item_type": "ADD_ON",
                    "item_id": "addon-x",
                    "name": "Extra",
                    "quantity": 1,
                    "unit_price": "9.00",
                    "line_total": "9.00",
                }
            ],
            "total": "9.00",
            "currency": "EUR",
        }
        commands = _commands(storefront_service=service)

        reply = commands.apply_action(
            provider_id=PROVIDER,
            chat_ref=CHAT,
            action_data="subscription:remove:SUBSCRIPTION:plan-a",
        )

        service.remove_item.assert_called_once_with(
            PROVIDER, CHAT, "SUBSCRIPTION", "plan-a"
        )
        # Replies with the refreshed remove-list.
        assert reply.meta == {
            "kind": "bot_choices",
            "text": "Tap any item to remove it",
        }
        assert reply.choices[0].action_data == "subscription:remove:ADD_ON:addon-x"
