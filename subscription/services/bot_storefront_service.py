"""Bot storefront service (S53.0 / D8).

The single home for the bot commerce storefront's draft behaviour:

* mutate the per-chat draft — a tarif plan **replaces** any prior plan, while an
  add-on / token bundle **toggles** (add if absent, remove if present);
* mint a one-time, TTL'd opaque token on ``/checkout``;
* resolve a token to **recomputed** line items (names/prices read live from the
  catalogs — the draft persists only ``{item_type, item_id, quantity}``, never a
  price), enforcing single-use + expiry.

It owns NO charge logic and creates NO invoice/subscription — the browser
checkout (reached via the draft link) does all of that, exactly as today.

The line-item ``item_type`` values are the core ``LineItemType`` vocabulary:
SUBSCRIPTION / ADD_ON / TOKEN_BUNDLE.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional, Protocol

from vbwd.models.enums import LineItemType
from plugins.subscription.subscription.models import BotCheckoutDraft
from plugins.subscription.subscription.repositories.bot_checkout_draft_repository import (  # noqa: E501
    BotCheckoutDraftRepository,
)


ITEM_TYPE_SUBSCRIPTION = LineItemType.SUBSCRIPTION.value
ITEM_TYPE_ADD_ON = LineItemType.ADD_ON.value
ITEM_TYPE_TOKEN_BUNDLE = LineItemType.TOKEN_BUNDLE.value

DEFAULT_QUANTITY = 1
_TOKEN_BYTES = 24  # → 32-char urlsafe token (well under the 64-char column).


class _PricedCatalogItem(Protocol):
    """The narrow shape resolution needs from a plan / add-on catalog entry."""

    name: str
    price: Any
    currency: Any


class _TokenBundleItem(Protocol):
    """The narrow shape resolution needs from a token-bundle catalog entry."""

    name: str
    price: Any


class DraftResolutionError(LookupError):
    """Raised when a draft token is unknown, expired, or already redeemed.

    A clear typed error (never a silent ``None``) so the public endpoint can
    map it to a 404 — the security contract is single-use + expiring (Liskov:
    the service never returns a stale draft as if it were valid).
    """


class BotStorefrontService:
    """Mutates the bot checkout draft and resolves it for the browser handoff."""

    def __init__(
        self,
        draft_repository: BotCheckoutDraftRepository,
        *,
        checkout_draft_ttl_seconds: int,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._draft_repository = draft_repository
        self._ttl_seconds = checkout_draft_ttl_seconds
        self._clock = clock or datetime.utcnow

    # ── draft mutation ───────────────────────────────────────────────────────
    def set_plan(
        self, provider_id: str, chat_ref: str, plan_id: str
    ) -> BotCheckoutDraft:
        """Record the chosen plan — a single plan that **replaces** any prior."""
        draft = self._get_or_create_draft(provider_id, chat_ref)
        items = [
            item
            for item in self._items(draft)
            if item["item_type"] != ITEM_TYPE_SUBSCRIPTION
        ]
        items.append(self._line_item(ITEM_TYPE_SUBSCRIPTION, plan_id))
        return self._store_items(draft, items)

    def toggle_addon(
        self, provider_id: str, chat_ref: str, addon_id: str
    ) -> BotCheckoutDraft:
        """Add the add-on if absent, remove it if already selected (toggle)."""
        return self._toggle(provider_id, chat_ref, ITEM_TYPE_ADD_ON, addon_id)

    def toggle_token_bundle(
        self, provider_id: str, chat_ref: str, bundle_id: str
    ) -> BotCheckoutDraft:
        """Add the token bundle if absent, remove it if selected (toggle)."""
        return self._toggle(provider_id, chat_ref, ITEM_TYPE_TOKEN_BUNDLE, bundle_id)

    def get_draft(self, provider_id: str, chat_ref: str) -> Optional[BotCheckoutDraft]:
        """The current draft for a chat, if one exists."""
        return self._draft_repository.find_by_chat(provider_id, chat_ref)

    # ── /checkout: mint a one-time TTL token ─────────────────────────────────
    def mint_checkout_token(self, provider_id: str, chat_ref: str) -> Optional[str]:
        """Finalize the draft → set a random one-time token + TTL → return it.

        Returns ``None`` when the chat has no draft or an empty selection (the
        caller renders an "add something first" hint — no token, no link).
        """
        draft = self._draft_repository.find_by_chat(provider_id, chat_ref)
        if draft is None or not self._items(draft):
            return None

        token = secrets.token_urlsafe(_TOKEN_BYTES)
        draft.token = token
        draft.expires_at = self._clock() + timedelta(seconds=self._ttl_seconds)
        draft.redeemed_at = None
        self._draft_repository.save(draft)
        return token

    # ── public resolution: recompute from catalogs, single-use + expiring ────
    def resolve_token(
        self,
        token: str,
        *,
        plan_lookup: Callable[[str], Optional[_PricedCatalogItem]],
        addon_lookup: Callable[[str], Optional[_PricedCatalogItem]],
        bundle_lookup: Callable[[str], Optional[_TokenBundleItem]],
    ) -> List[dict]:
        """Resolve a draft token → recomputed line items, then redeem it.

        Prices/names are read live from the catalogs via the injected lookups —
        the persisted draft amounts (there are none) are never trusted. Expired
        or already-redeemed tokens raise :class:`DraftResolutionError` (→ 404).
        The token is single-use: it is marked redeemed on first resolution.
        """
        draft = self._draft_repository.find_by_token(token)
        if draft is None:
            raise DraftResolutionError("Unknown checkout-draft token")
        if draft.redeemed_at is not None:
            raise DraftResolutionError("Checkout-draft token already redeemed")
        if draft.expires_at is None or draft.expires_at <= self._clock():
            raise DraftResolutionError("Checkout-draft token expired")

        resolved = self._recompute_line_items(
            draft,
            plan_lookup=plan_lookup,
            addon_lookup=addon_lookup,
            bundle_lookup=bundle_lookup,
        )

        draft.redeemed_at = self._clock()
        self._draft_repository.save(draft)
        return resolved

    # ── internals ────────────────────────────────────────────────────────────
    def _recompute_line_items(
        self,
        draft: BotCheckoutDraft,
        *,
        plan_lookup: Callable[[str], Optional[_PricedCatalogItem]],
        addon_lookup: Callable[[str], Optional[_PricedCatalogItem]],
        bundle_lookup: Callable[[str], Optional[_TokenBundleItem]],
    ) -> List[dict]:
        resolved: List[dict] = []
        for item in self._items(draft):
            item_type = item["item_type"]
            item_id = item["item_id"]
            quantity = item.get("quantity", DEFAULT_QUANTITY)
            display = self._lookup_display(
                item_type,
                item_id,
                plan_lookup=plan_lookup,
                addon_lookup=addon_lookup,
                bundle_lookup=bundle_lookup,
            )
            if display is None:
                # A catalog entry vanished (deactivated/deleted) since the bot
                # tap — drop it rather than fabricate a price. The browser
                # checkout shows only items still purchasable today.
                continue
            resolved.append(
                {
                    "item_type": item_type,
                    "item_id": item_id,
                    "quantity": quantity,
                    "name": display["name"],
                    "unit_price": display["unit_price"],
                    "currency": display["currency"],
                }
            )
        return resolved

    def _lookup_display(
        self,
        item_type: str,
        item_id: str,
        *,
        plan_lookup: Callable[[str], Optional[_PricedCatalogItem]],
        addon_lookup: Callable[[str], Optional[_PricedCatalogItem]],
        bundle_lookup: Callable[[str], Optional[_TokenBundleItem]],
    ) -> Optional[dict]:
        if item_type == ITEM_TYPE_SUBSCRIPTION:
            plan = plan_lookup(item_id)
            if plan is None:
                return None
            return {
                "name": plan.name,
                "unit_price": str(plan.price) if plan.price is not None else None,
                "currency": plan.currency,
            }
        if item_type == ITEM_TYPE_ADD_ON:
            addon = addon_lookup(item_id)
            if addon is None:
                return None
            return {
                "name": addon.name,
                "unit_price": str(addon.price),
                "currency": addon.currency,
            }
        if item_type == ITEM_TYPE_TOKEN_BUNDLE:
            bundle = bundle_lookup(item_id)
            if bundle is None:
                return None
            return {
                "name": bundle.name,
                "unit_price": str(bundle.price),
                "currency": None,  # token bundles price in the system default.
            }
        return None

    def _toggle(
        self, provider_id: str, chat_ref: str, item_type: str, item_id: str
    ) -> BotCheckoutDraft:
        draft = self._get_or_create_draft(provider_id, chat_ref)
        items = self._items(draft)
        already_selected = any(
            item["item_type"] == item_type and item["item_id"] == item_id
            for item in items
        )
        if already_selected:
            items = [
                item
                for item in items
                if not (item["item_type"] == item_type and item["item_id"] == item_id)
            ]
        else:
            items.append(self._line_item(item_type, item_id))
        return self._store_items(draft, items)

    def _get_or_create_draft(self, provider_id: str, chat_ref: str) -> BotCheckoutDraft:
        draft = self._draft_repository.find_by_chat(provider_id, chat_ref)
        if draft is None:
            draft = BotCheckoutDraft(
                provider_id=provider_id,
                chat_ref=chat_ref,
                line_items=[],
            )
        return draft

    def _store_items(
        self, draft: BotCheckoutDraft, items: List[dict]
    ) -> BotCheckoutDraft:
        draft.line_items = items
        # A mutation reopens an already-checked-out draft for a fresh handoff:
        # the prior token is invalidated so the old link can no longer resolve.
        draft.token = None
        draft.expires_at = None
        draft.redeemed_at = None
        return self._draft_repository.save(draft)

    @staticmethod
    def _items(draft: BotCheckoutDraft) -> List[dict]:
        return list(draft.line_items or [])

    @staticmethod
    def _line_item(item_type: str, item_id: str) -> dict:
        return {
            "item_type": item_type,
            "item_id": item_id,
            "quantity": DEFAULT_QUANTITY,
        }
