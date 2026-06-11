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

ACTION_SELECT_PLAN = "plan"
ACTION_TOGGLE_ADDON = "addon"
ACTION_TOGGLE_BUNDLE = "bundle"

_ACTION_SEPARATOR = ":"

LINK_BALANCE_HINT = "Link your account to see your token balance."
EMPTY_CHECKOUT_HINT = "Your cart is empty — pick a plan, add-on, or token bundle first."


class CatalogItem(Protocol):
    """The narrow shape the storefront needs from any catalog entry (I-segregation)."""

    id: Any
    name: str


class _ReplyFactory(Protocol):
    """The neutral ``BotReply`` constructor, passed in to keep bot-base optional."""

    def __call__(self, *, text: str, choices: list) -> object:
        ...


class _ChoiceFactory(Protocol):
    """The neutral ``BotChoice`` constructor, passed in to keep bot-base optional."""

    def __call__(self, *, label: str, action_data: str) -> object:
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
            )
            for plan in self._active_plans()
        ]
        return self._reply(
            text="Choose a tarif plan (a new pick replaces the current one):",
            choices=choices,
        )

    def add_ons_reply(self) -> object:
        choices = [
            self._choice(
                label=addon.name,
                action_data=encode_action(ACTION_TOGGLE_ADDON, str(addon.id)),
            )
            for addon in self._active_addons()
        ]
        return self._reply(
            text="Tap an add-on to add or remove it:",
            choices=choices,
        )

    def tokens_reply(self, *, identity) -> object:
        """The catalog is anonymous; the balance line shows only when linked."""
        choices = [
            self._choice(
                label=bundle.name,
                action_data=encode_action(ACTION_TOGGLE_BUNDLE, str(bundle.id)),
            )
            for bundle in self._active_token_bundles()
        ]
        text = self._tokens_text(identity)
        return self._reply(text=text, choices=choices)

    def checkout_reply(self, *, provider_id: str, chat_ref: str) -> object:
        token = self._storefront_service.mint_checkout_token(provider_id, chat_ref)
        if token is None:
            return self._reply(text=EMPTY_CHECKOUT_HINT, choices=[])
        link = f"{self._checkout_link_base_url}/checkout?draft={token}"
        return self._reply(
            text=f"Tap to complete your purchase in the browser:\n{link}",
            choices=[],
        )

    # ── tapped choice → draft mutation ───────────────────────────────────────
    def apply_action(
        self, *, provider_id: str, chat_ref: str, action_data: str
    ) -> object:
        parsed = parse_action(action_data)
        if parsed is None:
            return self._reply(text="Unknown selection.", choices=[])
        action, item_id = parsed

        if action == ACTION_SELECT_PLAN:
            self._storefront_service.set_plan(provider_id, chat_ref, item_id)
            return self._reply(text="Plan selected.", choices=[])
        if action == ACTION_TOGGLE_ADDON:
            self._storefront_service.toggle_addon(provider_id, chat_ref, item_id)
            return self._reply(text="Add-on updated.", choices=[])
        if action == ACTION_TOGGLE_BUNDLE:
            self._storefront_service.toggle_token_bundle(provider_id, chat_ref, item_id)
            return self._reply(text="Token bundle updated.", choices=[])
        return self._reply(text="Unknown selection.", choices=[])

    # ── internals ────────────────────────────────────────────────────────────
    def _tokens_text(self, identity) -> str:
        catalog_prompt = "Tap a token bundle to add or remove it:"
        if identity is None or self._balance_reader is None:
            return f"{LINK_BALANCE_HINT}\n{catalog_prompt}"
        balance = self._balance_reader(identity)
        if balance is None:
            return f"{LINK_BALANCE_HINT}\n{catalog_prompt}"
        return f"You have {balance} tokens.\n{catalog_prompt}"
