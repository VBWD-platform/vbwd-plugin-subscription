"""Unit specs for the bot storefront DRAFT service (S53.0 / D8).

Pure logic — no DB. The repository is a MagicMock and the catalog lookups are
plain callables, so these specs pin the draft-mutation contract (plan REPLACES,
add-on/bundle TOGGLE), the one-time TTL token mint, and the recompute-from-
catalog resolution + single-use/expiry security.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from plugins.subscription.subscription.services.bot_storefront_service import (
    BotStorefrontService,
    DraftResolutionError,
    ITEM_TYPE_ADD_ON,
    ITEM_TYPE_SUBSCRIPTION,
    ITEM_TYPE_TOKEN_BUNDLE,
)

PROVIDER = "telegram"
CHAT = "4242"
TTL_SECONDS = 900


class _FakeDraftRepository:
    """A minimal in-memory stand-in honoring the repository contract."""

    def __init__(self):
        self._by_chat = {}
        self._by_token = {}

    def find_by_chat(self, provider_id, chat_ref):
        return self._by_chat.get((provider_id, chat_ref))

    def find_by_token(self, token):
        return self._by_token.get(token)

    def save(self, draft):
        self._by_chat[(draft.provider_id, draft.chat_ref)] = draft
        if draft.token:
            self._by_token[draft.token] = draft
        return draft


def _service(repo=None, clock=None):
    return BotStorefrontService(
        repo or _FakeDraftRepository(),
        checkout_draft_ttl_seconds=TTL_SECONDS,
        clock=clock,
    )


def _item_types(draft):
    return [item["item_type"] for item in draft.line_items]


def _item_ids(draft):
    return [item["item_id"] for item in draft.line_items]


class TestPlanReplaces:
    def test_set_plan_records_a_subscription_line_item(self):
        service = _service()
        draft = service.set_plan(PROVIDER, CHAT, "plan-a")
        assert _item_types(draft) == [ITEM_TYPE_SUBSCRIPTION]
        assert _item_ids(draft) == ["plan-a"]

    def test_second_plan_replaces_the_first(self):
        service = _service()
        service.set_plan(PROVIDER, CHAT, "plan-a")
        draft = service.set_plan(PROVIDER, CHAT, "plan-b")
        plan_items = [
            item
            for item in draft.line_items
            if item["item_type"] == ITEM_TYPE_SUBSCRIPTION
        ]
        assert len(plan_items) == 1
        assert plan_items[0]["item_id"] == "plan-b"

    def test_replacing_a_plan_keeps_addons(self):
        service = _service()
        service.set_plan(PROVIDER, CHAT, "plan-a")
        service.toggle_addon(PROVIDER, CHAT, "addon-x")
        draft = service.set_plan(PROVIDER, CHAT, "plan-b")
        assert "addon-x" in _item_ids(draft)
        assert "plan-b" in _item_ids(draft)
        assert "plan-a" not in _item_ids(draft)


class TestAddonToggles:
    def test_first_tap_adds_the_addon(self):
        service = _service()
        draft = service.toggle_addon(PROVIDER, CHAT, "addon-x")
        assert _item_types(draft) == [ITEM_TYPE_ADD_ON]
        assert _item_ids(draft) == ["addon-x"]

    def test_second_tap_removes_the_addon(self):
        service = _service()
        service.toggle_addon(PROVIDER, CHAT, "addon-x")
        draft = service.toggle_addon(PROVIDER, CHAT, "addon-x")
        assert draft.line_items == []

    def test_independent_addons_coexist(self):
        service = _service()
        service.toggle_addon(PROVIDER, CHAT, "addon-x")
        draft = service.toggle_addon(PROVIDER, CHAT, "addon-y")
        assert set(_item_ids(draft)) == {"addon-x", "addon-y"}


class TestTokenBundleToggles:
    def test_toggle_bundle_adds_then_removes(self):
        service = _service()
        added = service.toggle_token_bundle(PROVIDER, CHAT, "bundle-1")
        assert _item_types(added) == [ITEM_TYPE_TOKEN_BUNDLE]
        removed = service.toggle_token_bundle(PROVIDER, CHAT, "bundle-1")
        assert removed.line_items == []


class TestMintCheckoutToken:
    def test_mint_returns_token_and_sets_ttl(self):
        fixed_now = datetime(2026, 6, 10, 12, 0, 0)
        service = _service(clock=lambda: fixed_now)
        service.set_plan(PROVIDER, CHAT, "plan-a")

        token = service.mint_checkout_token(PROVIDER, CHAT)

        assert token
        draft = service.get_draft(PROVIDER, CHAT)
        assert draft.token == token
        assert draft.expires_at == fixed_now + timedelta(seconds=TTL_SECONDS)

    def test_mint_without_a_draft_returns_none(self):
        service = _service()
        assert service.mint_checkout_token(PROVIDER, CHAT) is None

    def test_mint_with_empty_selection_returns_none(self):
        service = _service()
        service.toggle_addon(PROVIDER, CHAT, "addon-x")
        service.toggle_addon(PROVIDER, CHAT, "addon-x")  # toggled back off

        assert service.mint_checkout_token(PROVIDER, CHAT) is None

    def test_tokens_are_unique_per_mint(self):
        service = _service()
        service.set_plan(PROVIDER, CHAT, "plan-a")
        first = service.mint_checkout_token(PROVIDER, CHAT)
        service.set_plan(PROVIDER, "other-chat", "plan-a")
        second = service.mint_checkout_token(PROVIDER, "other-chat")
        assert first != second


def _plan(name="Pro", price="9.99", currency="EUR"):
    return SimpleNamespace(name=name, price=price, currency=currency)


def _addon(name="Extra", price="2.00", currency="EUR"):
    return SimpleNamespace(name=name, price=price, currency=currency)


def _bundle(name="100 tokens", price="5.00"):
    return SimpleNamespace(name=name, price=price)


class TestResolveTokenRecomputesFromCatalog:
    def _mint(self, service):
        service.set_plan(PROVIDER, CHAT, "plan-a")
        service.toggle_addon(PROVIDER, CHAT, "addon-x")
        service.toggle_token_bundle(PROVIDER, CHAT, "bundle-1")
        return service.mint_checkout_token(PROVIDER, CHAT)

    def test_resolution_returns_recomputed_names_and_prices(self):
        service = _service()
        token = self._mint(service)

        resolved = service.resolve_token(
            token,
            plan_lookup=lambda _id: _plan(name="Pro", price="9.99"),
            addon_lookup=lambda _id: _addon(name="Extra", price="2.00"),
            bundle_lookup=lambda _id: _bundle(name="100 tokens", price="5.00"),
        )

        by_type = {item["item_type"]: item for item in resolved}
        assert by_type[ITEM_TYPE_SUBSCRIPTION]["name"] == "Pro"
        assert by_type[ITEM_TYPE_SUBSCRIPTION]["unit_price"] == "9.99"
        assert by_type[ITEM_TYPE_ADD_ON]["unit_price"] == "2.00"
        assert by_type[ITEM_TYPE_TOKEN_BUNDLE]["name"] == "100 tokens"
        # All carry the catalog item id + quantity (the only persisted data).
        assert by_type[ITEM_TYPE_SUBSCRIPTION]["item_id"] == "plan-a"
        assert by_type[ITEM_TYPE_SUBSCRIPTION]["quantity"] == 1

    def test_persisted_draft_holds_no_prices(self):
        service = _service()
        self._mint(service)
        draft = service.get_draft(PROVIDER, CHAT)
        for item in draft.line_items:
            assert set(item.keys()) == {"item_type", "item_id", "quantity"}

    def test_vanished_catalog_item_is_dropped_not_priced(self):
        service = _service()
        token = self._mint(service)

        resolved = service.resolve_token(
            token,
            plan_lookup=lambda _id: None,  # plan deactivated since the tap
            addon_lookup=lambda _id: _addon(),
            bundle_lookup=lambda _id: _bundle(),
        )

        assert ITEM_TYPE_SUBSCRIPTION not in {item["item_type"] for item in resolved}


class TestResolveTokenSecurity:
    def _mint(self, service):
        service.set_plan(PROVIDER, CHAT, "plan-a")
        return service.mint_checkout_token(PROVIDER, CHAT)

    def _resolve(self, service, token):
        return service.resolve_token(
            token,
            plan_lookup=lambda _id: _plan(),
            addon_lookup=lambda _id: _addon(),
            bundle_lookup=lambda _id: _bundle(),
        )

    def test_unknown_token_raises(self):
        service = _service()
        with pytest.raises(DraftResolutionError):
            self._resolve(service, "nope")

    def test_expired_token_raises(self):
        now = {"value": datetime(2026, 6, 10, 12, 0, 0)}
        service = _service(clock=lambda: now["value"])
        token = self._mint(service)
        now["value"] = now["value"] + timedelta(seconds=TTL_SECONDS + 1)

        with pytest.raises(DraftResolutionError):
            self._resolve(service, token)

    def test_token_is_single_use(self):
        service = _service()
        token = self._mint(service)
        self._resolve(service, token)  # first use ok

        with pytest.raises(DraftResolutionError):
            self._resolve(service, token)  # second use 404s


def _lookups():
    return dict(
        plan_lookup=lambda _id: _plan(price="29.00"),
        addon_lookup=lambda _id: _addon(price="9.00"),
        bundle_lookup=lambda _id: _bundle(price="5.00"),
    )


class TestClearDraft:
    def test_clear_draft_empties_the_line_items(self):
        service = _service()
        service.set_plan(PROVIDER, CHAT, "plan-a")
        service.toggle_addon(PROVIDER, CHAT, "addon-x")

        draft = service.clear_draft(PROVIDER, CHAT)

        assert draft.line_items == []

    def test_clear_draft_with_no_draft_is_a_noop_empty(self):
        service = _service()
        draft = service.clear_draft(PROVIDER, CHAT)
        assert draft.line_items == []


class TestRemoveItem:
    def test_remove_item_drops_the_matching_line(self):
        service = _service()
        service.set_plan(PROVIDER, CHAT, "plan-a")
        service.toggle_addon(PROVIDER, CHAT, "addon-x")

        draft = service.remove_item(PROVIDER, CHAT, ITEM_TYPE_ADD_ON, "addon-x")

        assert _item_types(draft) == [ITEM_TYPE_SUBSCRIPTION]
        assert _item_ids(draft) == ["plan-a"]

    def test_remove_item_absent_is_a_noop(self):
        service = _service()
        service.set_plan(PROVIDER, CHAT, "plan-a")

        draft = service.remove_item(PROVIDER, CHAT, ITEM_TYPE_ADD_ON, "ghost")

        assert _item_types(draft) == [ITEM_TYPE_SUBSCRIPTION]


class TestComputeCart:
    def test_empty_draft_is_an_empty_cart(self):
        service = _service()
        cart = service.compute_cart(PROVIDER, CHAT, **_lookups())
        assert cart["items"] == []
        assert cart["total"] == "0"

    def test_recomputes_prices_from_catalog_not_persisted(self):
        service = _service()
        service.set_plan(PROVIDER, CHAT, "plan-a")
        service.toggle_addon(PROVIDER, CHAT, "addon-x")

        cart = service.compute_cart(PROVIDER, CHAT, **_lookups())

        names = {item["name"] for item in cart["items"]}
        assert names == {"Pro", "Extra"}
        plan_line = next(i for i in cart["items"] if i["name"] == "Pro")
        assert plan_line["unit_price"] == "29.00"
        assert plan_line["quantity"] == 1
        assert plan_line["line_total"] == "29.00"
        # 29.00 + 9.00 = 38.00
        assert cart["total"] == "38.00"
        assert cart["currency"] == "EUR"

    def test_vanished_catalog_entry_is_dropped(self):
        service = _service()
        service.set_plan(PROVIDER, CHAT, "plan-a")

        cart = service.compute_cart(
            PROVIDER,
            CHAT,
            plan_lookup=lambda _id: None,
            addon_lookup=lambda _id: None,
            bundle_lookup=lambda _id: None,
        )

        assert cart["items"] == []
        assert cart["total"] == "0"
