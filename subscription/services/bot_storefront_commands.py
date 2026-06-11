"""Bot storefront command handlers (S53.0).

Provider-neutral logic for the four storefront commands and their tapped
choices. It depends only on:

* the :class:`BotStorefrontService` (draft mutation + token mint),
* three catalog readers (active tarif plans / add-ons / token bundles) returning
  objects with ``id`` + ``name``,
* an optional balance reader (linked chats only — the only identity-aware path).

It references **no** transport type and builds replies purely from the neutral
``BotReply`` / ``BotChoice`` DTOs (imported lazily by the caller and passed in as
factories so this module never hard-imports bot-base — the bridge stays
optional, exactly like the chat/taro consumers).

``action_data`` is namespaced ``"subscription:<action>:<item_id>"`` so the
bridge routes a tapped choice back to ``handle_action`` here (D7).
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional, Protocol

NAMESPACE = "subscription"

TARIFS_COMMAND = "tarifs"
ADD_ONS_COMMAND = "add-ons"
TOKENS_COMMAND = "tokens"
CHECKOUT_COMMAND = "checkout"
CART_COMMAND = "cart"
CART_CLEAR_COMMAND = "cart-clear"
CART_EDIT_COMMAND = "cart-edit"

ACTION_SELECT_PLAN = "plan"
ACTION_TOGGLE_ADDON = "addon"
ACTION_TOGGLE_BUNDLE = "bundle"
ACTION_REMOVE = "remove"

_ACTION_SEPARATOR = ":"

LINK_BALANCE_HINT = "Link your account to see your token balance."
EMPTY_CHECKOUT_HINT = "Your cart is empty — pick a plan, add-on, or token bundle first."
EMPTY_CART_TEXT = "Your cart is empty — pick a plan, add-on, or token bundle first."
REMOVE_PROMPT_TEXT = "Tap any item to remove it"

# Clean, body-free prompts shown on rich card clients via ``meta.text`` (the
# numbered list stays in the plain ``body`` for non-rich clients).
TARIFS_PROMPT = "Choose a tarif plan (a new pick replaces the current one):"
ADD_ONS_PROMPT = "Tap an add-on to add or remove it:"
TOKENS_PROMPT = "Tap a token bundle to add or remove it:"

# Map a ``billing_period`` value onto the compact hint suffix ("€29/mo").
_BILLING_SUFFIX = {
    "MONTHLY": "/mo",
    "YEARLY": "/yr",
    "QUARTERLY": "/qtr",
    "WEEKLY": "/wk",
    "DAILY": "/day",
}
_CURRENCY_SYMBOL = {"EUR": "€", "USD": "$", "GBP": "£"}


class CatalogItem(Protocol):
    """The narrow shape the storefront needs from any catalog entry (I-segregation)."""

    id: Any
    name: str


class _ReplyFactory(Protocol):
    """The neutral ``BotReply`` constructor, passed in to keep bot-base optional."""

    def __call__(
        self, *, text: str, choices: list, meta: Optional[dict] = ...
    ) -> object:
        ...


class _ChoiceFactory(Protocol):
    """The neutral ``BotChoice`` constructor, passed in to keep bot-base optional."""

    def __call__(
        self, *, label: str, action_data: str, hint: Optional[str] = ...
    ) -> object:
        ...


def encode_action(action: str, item_id: str) -> str:
    """Build the namespaced ``action_data`` for a tappable choice (D7)."""
    return _ACTION_SEPARATOR.join((NAMESPACE, action, item_id))


def parse_action(action_data: str) -> Optional[tuple]:
    """Split ``"subscription:<action>:<item_id>"`` → ``(action, item_id)``.

    Returns ``None`` for anything not owned by this namespace (defensive — the
    bridge already routes by namespace, but the handler never trusts that).
    """
    parts = action_data.split(_ACTION_SEPARATOR, 2)
    if len(parts) != 3 or parts[0] != NAMESPACE:
        return None
    return parts[1], parts[2]


class BotStorefrontCommands:
    """Builds the storefront replies + applies tapped choices to the draft."""

    def __init__(
        self,
        *,
        storefront_service,
        active_plans: Callable[[], List[CatalogItem]],
        active_addons: Callable[[], List[CatalogItem]],
        active_token_bundles: Callable[[], List[CatalogItem]],
        checkout_link_base_url: str,
        reply_factory: _ReplyFactory,
        choice_factory: _ChoiceFactory,
        balance_reader: Optional[Callable[[object], Optional[int]]] = None,
    ) -> None:
        self._storefront_service = storefront_service
        self._active_plans = active_plans
        self._active_addons = active_addons
        self._active_token_bundles = active_token_bundles
        self._checkout_link_base_url = checkout_link_base_url.rstrip("/")
        self._reply = reply_factory
        self._choice = choice_factory
        self._balance_reader = balance_reader

    # ── command replies ──────────────────────────────────────────────────────
    def tarifs_reply(self) -> object:
        choices = [
            self._choice(
                label=plan.name,
                action_data=encode_action(ACTION_SELECT_PLAN, str(plan.id)),
                hint=self._recurring_hint(plan),
            )
            for plan in self._active_plans()
        ]
        return self._choices_reply(text=TARIFS_PROMPT, choices=choices)

    def add_ons_reply(self) -> object:
        choices = [
            self._choice(
                label=addon.name,
                action_data=encode_action(ACTION_TOGGLE_ADDON, str(addon.id)),
                hint=self._recurring_hint(addon),
            )
            for addon in self._active_addons()
        ]
        return self._choices_reply(text=ADD_ONS_PROMPT, choices=choices)

    def tokens_reply(self, *, identity) -> object:
        """The catalog is anonymous; the balance line shows only when linked."""
        choices = [
            self._choice(
                label=bundle.name,
                action_data=encode_action(ACTION_TOGGLE_BUNDLE, str(bundle.id)),
                hint=self._flat_price_hint(bundle),
            )
            for bundle in self._active_token_bundles()
        ]
        text = self._tokens_text(identity)
        return self._choices_reply(text=text, choices=choices)

    def checkout_reply(self, *, provider_id: str, chat_ref: str) -> object:
        token = self._storefront_service.mint_checkout_token(provider_id, chat_ref)
        if token is None:
            return self._reply(text=EMPTY_CHECKOUT_HINT, choices=[])
        link = f"{self._checkout_link_base_url}/checkout?draft={token}"
        return self._reply(
            text=f"Tap to complete your purchase in the browser:\n{link}",
            choices=[],
        )

    # ── cart family (S70.3) ──────────────────────────────────────────────────
    def cart_reply(self, *, provider_id: str, chat_ref: str) -> object:
        """A ``bot_cart`` summary recomputed from the current draft."""
        cart = self._compute_cart(provider_id, chat_ref)
        return self._cart_reply_from(cart)

    def cart_clear_reply(self, *, provider_id: str, chat_ref: str) -> object:
        """Empty the draft, then reply with the now-empty ``bot_cart``."""
        self._storefront_service.clear_draft(provider_id, chat_ref)
        cart = self._compute_cart(provider_id, chat_ref)
        return self._cart_reply_from(cart)

    def cart_edit_reply(self, *, provider_id: str, chat_ref: str) -> object:
        """A ``bot_choices`` list — each draft line a remove choice. An empty
        draft falls back to the empty-cart state."""
        cart = self._compute_cart(provider_id, chat_ref)
        return self._cart_edit_from(cart)

    # ── tapped choice → draft mutation ───────────────────────────────────────
    def apply_action(
        self, *, provider_id: str, chat_ref: str, action_data: str
    ) -> object:
        parsed = parse_action(action_data)
        if parsed is None:
            return self._reply(text="Unknown selection.", choices=[])
        action, remainder = parsed

        # After any add/toggle the user sees the running cart card (line items +
        # total + checkout button) so they can check out immediately, instead of
        # a terse confirmation. Reuse ``cart_reply`` (server-recomputed prices).
        if action == ACTION_SELECT_PLAN:
            self._storefront_service.set_plan(provider_id, chat_ref, remainder)
            return self.cart_reply(provider_id=provider_id, chat_ref=chat_ref)
        if action == ACTION_TOGGLE_ADDON:
            self._storefront_service.toggle_addon(provider_id, chat_ref, remainder)
            return self.cart_reply(provider_id=provider_id, chat_ref=chat_ref)
        if action == ACTION_TOGGLE_BUNDLE:
            self._storefront_service.toggle_token_bundle(
                provider_id, chat_ref, remainder
            )
            return self.cart_reply(provider_id=provider_id, chat_ref=chat_ref)
        if action == ACTION_REMOVE:
            return self._apply_remove(provider_id, chat_ref, remainder)
        return self._reply(text="Unknown selection.", choices=[])

    # ── internals ────────────────────────────────────────────────────────────
    def _apply_remove(self, provider_id: str, chat_ref: str, remainder: str) -> object:
        """``remove`` carries ``<item_type>:<item_id>``; drop it then reply with
        the refreshed remove-list (or the empty-cart state)."""
        item_type, _, item_id = remainder.partition(_ACTION_SEPARATOR)
        if not item_type or not item_id:
            return self._reply(text="Unknown selection.", choices=[])
        self._storefront_service.remove_item(provider_id, chat_ref, item_type, item_id)
        cart = self._compute_cart(provider_id, chat_ref)
        return self._cart_edit_from(cart)

    def _choices_reply(self, *, text: str, choices: list) -> object:
        """A choice reply carrying the clean ``meta.text`` prompt for rich
        clients (the numbered fallback body is added by each provider sender)."""
        return self._reply(
            text=text,
            choices=choices,
            meta={"kind": "bot_choices", "text": text},
        )

    def _cart_reply_from(self, cart: dict) -> object:
        return self._reply(
            text=self._cart_body(cart),
            choices=[],
            meta={
                "kind": "bot_cart",
                "items": [
                    {
                        "name": item["name"],
                        "quantity": item["quantity"],
                        "unit_price": item["unit_price"],
                        "line_total": item["line_total"],
                    }
                    for item in cart["items"]
                ],
                "total": cart["total"],
                "currency": cart["currency"],
            },
        )

    def _cart_edit_from(self, cart: dict) -> object:
        if not cart["items"]:
            return self._cart_reply_from(cart)
        choices = [
            self._choice(
                label=f"{item['name']} ×{item['quantity']} — {item['unit_price']}",
                action_data=encode_action(
                    ACTION_REMOVE,
                    f"{item['item_type']}{_ACTION_SEPARATOR}{item['item_id']}",
                ),
            )
            for item in cart["items"]
        ]
        return self._reply(
            text=REMOVE_PROMPT_TEXT,
            choices=choices,
            meta={"kind": "bot_choices", "text": REMOVE_PROMPT_TEXT},
        )

    def _compute_cart(self, provider_id: str, chat_ref: str) -> dict:
        return self._storefront_service.compute_cart(
            provider_id,
            chat_ref,
            plan_lookup=self._plan_lookup,
            addon_lookup=self._addon_lookup,
            bundle_lookup=self._bundle_lookup,
        )

    @staticmethod
    def _cart_body(cart: dict) -> str:
        """The plain-text cart fallback for non-rich clients."""
        if not cart["items"]:
            return EMPTY_CART_TEXT
        lines = ["Your cart:"]
        for item in cart["items"]:
            lines.append(f"- {item['name']} ×{item['quantity']} — {item['line_total']}")
        lines.append(f"Total: {cart['total']} {cart['currency']}")
        return "\n".join(lines)

    def _plan_lookup(self, item_id: str):
        return self._find_by_id(self._active_plans(), item_id)

    def _addon_lookup(self, item_id: str):
        return self._find_by_id(self._active_addons(), item_id)

    def _bundle_lookup(self, item_id: str):
        return self._find_by_id(self._active_token_bundles(), item_id)

    @staticmethod
    def _find_by_id(items, item_id: str):
        for item in items:
            if str(item.id) == str(item_id):
                return item
        return None

    @classmethod
    def _recurring_hint(cls, item) -> Optional[str]:
        """A price hint with a billing suffix ("€29/mo") for plans/add-ons.

        Read defensively (``getattr``) so a catalog entry without price fields
        simply carries no hint — non-priced callers/tests are unaffected."""
        price_text = cls._price_text(item)
        if price_text is None:
            return None
        billing_period = getattr(item, "billing_period", None)
        billing_key = getattr(billing_period, "value", billing_period)
        suffix = _BILLING_SUFFIX.get(str(billing_key), "") if billing_period else ""
        return f"{price_text}{suffix}"

    @classmethod
    def _flat_price_hint(cls, item) -> Optional[str]:
        """A flat price hint ("€20") for token bundles (no billing period)."""
        return cls._price_text(item)

    @staticmethod
    def _price_text(item) -> Optional[str]:
        price = getattr(item, "price", None)
        if price is None:
            return None
        currency = getattr(item, "currency", None) or "EUR"
        symbol = _CURRENCY_SYMBOL.get(currency, f"{currency} ")
        # Drop a trailing ".00" so "29.00" → "29" (compact hint).
        amount = str(price)
        if amount.endswith(".00"):
            amount = amount[:-3]
        return f"{symbol}{amount}"

    def _tokens_text(self, identity) -> str:
        if identity is None or self._balance_reader is None:
            return f"{LINK_BALANCE_HINT}\n{TOKENS_PROMPT}"
        balance = self._balance_reader(identity)
        if balance is None:
            return f"{LINK_BALANCE_HINT}\n{TOKENS_PROMPT}"
        return f"You have {balance} tokens.\n{TOKENS_PROMPT}"
