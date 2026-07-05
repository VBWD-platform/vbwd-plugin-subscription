# Subscription Plugin — Architecture (backend)

How the subscription backend plugin works end-to-end, and how it stays
decoupled from the agnostic VBWD core.

- [1. The agnostic-core contract](#1-the-agnostic-core-contract)
- [2. Layered structure](#2-layered-structure)
- [3. Data model](#3-data-model)
- [4. Plugin wiring (`__init__.py`)](#4-plugin-wiring-__init__py)
- [5. Core ports & registries](#5-core-ports--registries)
- [6. Checkout → invoice → activation flow](#6-checkout--invoice--activation-flow)
- [7. Recurring billing & payment webhooks](#7-recurring-billing--payment-webhooks)
- [8. Lifecycle, scheduler & events](#8-lifecycle-scheduler--events)
- [9. Entitlements & access levels](#9-entitlements--access-levels)
- [10. Permissions](#10-permissions)
- [11. Configuration](#11-configuration)
- [12. Testing](#12-testing)

---

## 1. The agnostic-core contract

VBWD core (`vbwd-backend/vbwd/`) must not name any subscription concept. It
exposes **extension seams** — DI providers, an event dispatcher, an EventBus,
and a handful of *registries* (singletons with `register_*` / `clear_*`
functions). This plugin implements those seams in `__init__.py`. The result:

- Core can compute an invoice total, deletion dependencies, or recurring charges
  **without importing** plans/subscriptions.
- A subscription-free deployment still boots; the `subscription` migrations and
  registrations simply never run.

This is the inverse of a monolith: **the plugin reaches into core, never the
reverse.**

## 2. Layered structure

Classic Routes → Services → Repositories → Models, all inside the `subscription/`
source package (named after the plugin id, not `src/`):

| Layer | Dir | Responsibility |
|---|---|---|
| Routes | `subscription/routes/` | HTTP endpoints on `subscription_bp`; validation; auth/permission checks |
| Services | `subscription/services/` | Business logic **and** core-port implementations |
| Repositories | `subscription/repositories/` | Data access (one per aggregate) |
| Models | `subscription/models/` | SQLAlchemy ORM, extend core `BaseModel` (UUID PK, timestamps, version) |
| Handlers | `subscription/handlers/` | React to core events / line items |

Services double as **port implementations** where the core defines an interface
(e.g. `SubscriptionLifecycle`, `SubscriptionEntitlementProvider`,
`SubscriptionReadModel`, `CatalogReadModel`).

## 3. Data model

All tables are prefixed `vbwd_` and extend `BaseModel` (UUID `id`, `created_at`,
`updated_at`, `version`).

```
TarifPlanCategory ──< (many-to-many) >── TarifPlan ──< Subscription >── User (core)
                       is_single            │                              │
                                            │                              │
                                   AddOn ──< AddOnSubscription >───────────┘
                                  (tarif_plans M2M)
```

| Model | Table | Notable fields |
|---|---|---|
| `TarifPlan` | `vbwd_tarif_plan` | `name`, `slug`, `price`/`price_float`/`currency`, `billing_period`, `features` (JSON), `trial_days`, `is_active`, `sort_order` |
| `TarifPlanCategory` | `vbwd_tarif_plan_category` | `name`, `slug`, `parent_id`, **`is_single`** (one active plan per user in this category), `sort_order` |
| `Subscription` | `vbwd_subscription` | `user_id`, `tarif_plan_id`, `pending_plan_id`, `status`, `started_at`, `expires_at`, `trial_end_at`, `cancelled_at`, `paused_at`, `payment_failed_at`, `provider_subscription_id` |
| `AddOn` | `vbwd_addon` | `name`, `slug`, `price`, `currency`, `billing_period`, `config` (JSONB), `is_active`; M2M `tarif_plans` |
| `AddOnSubscription` | `vbwd_addon_subscription` | `user_id`, `addon_id`, `subscription_id`, `invoice_id`, `status`, `starts_at`, `expires_at`, `provider_subscription_id` |

**Invoices stay in core.** A subscription is linked to an invoice **only** via a
core `InvoiceLineItem` of `item_type=SUBSCRIPTION` whose `item_id == subscription.id`
— there is no `subscription_id`/`plan_id` column on `UserInvoice` (removed in
Sprint 11). Anything that needs "the invoice's subscription" walks the line items
(see `line_item_handler.resolve_catalog_item_id` and `subscription_lifecycle`).

`SubscriptionStatus` / `LineItemType` / `TokenTransactionType` enums live in core
(`vbwd.models.enums`) because the invoice engine references them generically.

## 4. Plugin wiring (`__init__.py`)

`SubscriptionPlugin(BasePlugin)` is the whole integration surface. Lifecycle
hooks the host calls:

| Hook | When | What it does |
|---|---|---|
| `initialize(config)` | load | merge `config` over `DEFAULT_CONFIG` |
| `get_blueprint()` | boot | return `subscription_bp` |
| `get_url_prefix()` | boot | `""` — routes carry absolute `/api/v1/...` paths |
| `on_enable()` | enable | register **all** DI providers, handlers, and core ports (see §5); self-heal the checkout CMS page; start the scheduler (skipped under `TESTING`) |
| `on_disable()` | disable | `clear_*` every registry it registered |
| `register_event_handlers(bus)` | boot | subscribe activation + access-level handlers to the EventBus |
| `register_line_item_handlers(reg)` | boot | add `SubscriptionLineItemHandler` |
| `register_categories()` | boot | declare the default "Subscription Plans" CMS category |
| `admin_permissions` / `user_permissions` | boot | declare `subscription.*` permission keys |

> **Scheduler + tests:** the expiry scheduler is started only when
> `current_app.config["TESTING"]` is false. Each test builds its own app and runs
> `on_enable`; an unguarded scheduler would leak a thread (and DB connections)
> per test. Mirror this guard for any new background work.

## 5. Core ports & registries

Everything `on_enable` registers, and the core module that owns each seam:

| Core seam (module) | Plugin implementation | Purpose |
|---|---|---|
| DI container providers | `repositories/*` | `subscription_repository`, `addon_repository`, `addon_subscription_repository`, `tarif_plan_repository`, `tarif_plan_category_repository` |
| `vbwd.events.line_item_registry` | `handlers/line_item_handler.py` | activate/reverse/restore `SUBSCRIPTION` + `ADD_ON` lines; recurring info |
| event dispatcher `checkout.requested` | `handlers/checkout_handler.py` | build subscription/add-on rows + invoice from a checkout request |
| event dispatcher `subscription.cancelled` | `handlers/cancel_handler.py` | downstream cancellation side-effects |
| `vbwd.events.bus` `subscription.activated` | `handlers/subscription_handlers.py`, `handlers/access_level_handler.py` (+ `services/plan_feature_access_level_service.py`) | receipts + access-level auto-assign (plan-linked **and** `plan.features["access_levels"]`) |
| `vbwd.events.bus` `subscription.cancelled` | `handlers/access_level_handler.py` (+ `services/plan_feature_access_level_service.py`) | revoke access levels (overlap-safe for feature-declared levels) |
| `vbwd.services.entitlement` | `services/subscription_entitlement_provider.py` | what an active subscription grants |
| `vbwd.services.subscription_read_model` | `services/subscription_read_model.py` | read-only subscription queries for core (e.g. user-deletion count) |
| `vbwd.services.catalog_read_model` | `services/catalog_read_model.py` | read-only catalog queries for core |
| `vbwd.services.subscription_lifecycle` (`ISubscriptionLifecycle`) | `services/subscription_lifecycle.py` | provider-webhook writes (link/renew/cancel/fail) |
| `vbwd.services.deletion_dependency_registry` | inline in `__init__.py` | "this user has N subscriptions" for the `/deletion-info` endpoint |
| `vbwd.services.demo_data_registry` | `subscription/demo_seed.py` | catalog seeder, test-data seeder, test-data cleaner |

If you add a new registration, add the matching `clear_*`/`unregister_*` in
`on_disable` — leaving stale singletons breaks the next enable/disable cycle and
the full test suite.

## 6. Checkout → invoice → activation flow

```
POST /api/v1/user/checkout         (routes/user_checkout.py)
        │  plan_id + token_bundle_ids[] + add_on_ids[] (+ payment_method_code)
        ▼
CheckoutHandler  (handlers/checkout_handler.py)   ← event "checkout.requested"
        │  creates Subscription(PENDING) + AddOnSubscription(PENDING) rows,
        │  builds a core UserInvoice whose line items reference them
        ▼
Payment provider pays the invoice  (core payment engine + a payment plugin)
        ▼
Core iterates invoice line items → LineItemRegistry
        ▼
SubscriptionLineItemHandler.activate_line_item(...)
        │  SUBSCRIPTION → status ACTIVE, set expires_at, credit plan tokens,
        │                 cancel conflicting plans in is_single categories,
        │                 publish "subscription.activated"
        │  ADD_ON       → status ACTIVE, set activated_at
        ▼
EventBus "subscription.activated" → receipt email + access-level assignment
```

Refunds/refund-reversals run the same handler's `reverse_line_item` /
`restore_line_item`, keeping token credits and statuses symmetric.

## 7. Recurring billing & payment webhooks

Two independent seams keep payment plugins subscription-agnostic:

1. **Recurring declaration** — `SubscriptionLineItemHandler.is_recurring_line_item`
   and `recurring_billing_spec` tell the provider, per line item, whether to set
   up a recurring charge and with what `(name, billing_period)`. One-off items and
   non-subscription items return `False`/`None`.

2. **Webhook writes** — `SubscriptionLifecycle` (`ISubscriptionLifecycle`) is what
   a provider calls from its webhook handler:
   - `link_provider_subscription(invoice_id, provider_sub_id)` — store the
     provider's subscription id on our `Subscription` (found via the invoice's
     SUBSCRIPTION line item).
   - `record_provider_renewal(...)` — create a new renewal `UserInvoice` +
     SUBSCRIPTION line item (idempotent via `provider_reference`).
   - `cancel_by_provider_subscription_id(...)` / `mark_provider_payment_failed(...)`
     / `mark_invoice_payment_failed(...)` — emit core `SubscriptionCancelledEvent`
     / `PaymentFailedEvent` for downstream handlers.

   `stripe`/`paypal`/`yookassa` import **only** `ISubscriptionLifecycle` from core —
   never this plugin.

## 8. Lifecycle, scheduler & events

- **Scheduler** (`scheduler.py`) runs every `expiration_check_interval_seconds`
  to expire lapsed subscriptions and drive dunning at `dunning_intervals_days`.
  Started from `on_enable`, guarded off under `TESTING`.
- **Domain events** (`events.py`) define payloads; the event dispatcher routes
  `checkout.requested` and `subscription.cancelled` to their handlers; the EventBus
  fans out `subscription.activated` / `subscription.cancelled` to email + access
  level handlers.
- **Email** templates in `templates/email/` cover activation, receipt, renewal
  reminder, payment failed, and cancellation. The `email` plugin dependency
  renders/sends them.

## 9. Entitlements & access levels

- `SubscriptionEntitlementProvider` answers core's entitlement queries — what an
  active subscription unlocks (feature flags / token grants from `plan.features`).
- `SubscriptionAccessLevelHandler` listens on the EventBus and assigns/revokes a
  user's access levels when a subscription activates/cancels. There are **two
  independent sources**, both applied by the same handler on `subscription.activated`
  / `subscription.cancelled`:

  1. **Plan-linked level** — an `AccessLevel` whose `linked_plan_slug` matches the
     plan's slug. One level per plan; the linkage lives on the access-level record
     and is surfaced in fe-admin via the `linked_plan_slug` field.

  2. **Feature-declared levels (the automatic access-level switch)** — access
     levels named directly in the **plan's Features field**. An admin adds one
     line to *Features (one per line)*:

     ```
     access_levels: premium, vip
     ```

     which the fe-admin parser stores as `plan.features == {"access_levels": "premium, vip"}`.
     On activation the user is granted every named access level (looked up by
     slug); on **cancellation or expiry** each is revoked **overlap-safe** — a
     level is kept if any *other* still-active plan of the user also declares it.
     The named access levels must already exist as `vbwd_access_level` records.
     All other Features lines remain plain display bullets.

  The handler's end-of-subscription revoke (`on_subscription_ended`) is
  subscribed to **both** `subscription.cancelled` and `subscription.expired`, and
  the user-facing cancel route publishes `subscription.cancelled` (like the admin
  route) so a user-initiated cancel actually triggers the revoke.

  `PlanFeatureAccessLevelService`
  (`services/plan_feature_access_level_service.py`) owns the parse + grant +
  overlap-safe revoke for source #2; the handler delegates to it and commits its
  own session. Both sources reach core only through the agnostic
  `UserAccessLevelService` (`find_by_slug` / `assign` / `revoke`).

> **Provenance caveat:** grants go through the shared user↔access-level
> association, which carries no per-source provenance. If an admin *manually*
> assigns a level that a plan also declares, cancelling that plan (with no other
> active plan declaring the level) will revoke the manual grant too. This matches
> the existing `linked_plan_slug` revoke behavior.

## 10. Permissions

Declared on the plugin and enforced in routes / by both frontends:

**Admin:** `subscription.plans.view`, `subscription.plans.manage`,
`subscription.subscriptions.view`, `subscription.subscriptions.manage`,
`subscription.addons.manage`, `subscription.configure`.

**User:** `subscription.plans.view`, `subscription.manage`,
`subscription.invoices.view`, `subscription.tokens.view`,
`subscription.tokens.manage` (plus the baseline `user.profile.*`).

## 11. Configuration

`config.json` (defaults also in `DEFAULT_CONFIG`):

| Key | Default | Meaning |
|---|---|---|
| `trial_days` | 14 | default trial length (0 = none) |
| `dunning_intervals_days` | `[3, 7]` | days after payment failure to remind |
| `expiration_check_interval_seconds` | 60 | scheduler tick |
| `max_subscriptions_per_user` | 10 | concurrent subscription cap |
| `allow_downgrade` | true | permit downgrade to cheaper plan |
| `proration_enabled` | true | prorate credits on plan change |

`admin-config.json` is the schema the admin Settings UI renders (General +
Lifecycle tabs). Every plugin ships both `config.json` and `admin-config.json`.

## 12. Testing

```bash
docker compose run --rm test python -m pytest plugins/subscription/tests/unit/ -v
docker compose run --rm test python -m pytest plugins/subscription/tests/integration/ -v
```

- **Unit** tests mock repositories (`MagicMock`) — no DB.
- **Integration** tests use the `db` fixture (creates/drops a `_test` DB) and
  cover DI provider registration, invoice↔subscription linkage, trial expiry, the
  tarif-plan repo, and a **schema characterisation** test
  (`_schema_fingerprint.json`) that guards the table shape.
- Always seed test/demo data through services (`demo_seed.py`) — never raw SQL.

See [`EXTENDING.md`](EXTENDING.md) for change recipes and
[`LLM_GUIDE.md`](LLM_GUIDE.md) for the compact map.
