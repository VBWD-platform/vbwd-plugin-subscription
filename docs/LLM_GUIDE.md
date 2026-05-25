# Subscription Plugin — LLM Guide (backend)

Compact, machine-oriented map for agents editing this plugin. Pair with
[`ARCHITECTURE.md`](ARCHITECTURE.md) (narrative) and [`EXTENDING.md`](EXTENDING.md)
(recipes). Paths are relative to `vbwd-backend/plugins/subscription/`.

## Identity
- plugin id: `subscription`; package dir: `subscription/` (NOT `src/`).
- depends on: `email` plugin.
- blueprint: `subscription_bp`, url_prefix `""` → routes use absolute `/api/v1/...`.
- owns tables: `vbwd_subscription`, `vbwd_tarif_plan`, `vbwd_tarif_plan_category`,
  `vbwd_addon`, `vbwd_addon_subscription`. (Invoices stay in core.)

## Invariants (do not violate)
1. Never edit `vbwd-backend/vbwd/` to add subscription behaviour. Core is agnostic.
2. Invoice↔subscription link = core `InvoiceLineItem` with
   `item_type=SUBSCRIPTION`, `item_id==subscription.id`. There is **no**
   `subscription_id`/`plan_id` column on `UserInvoice`. Walk line items.
3. Every `register_*` in `on_enable` has a `clear_*`/`unregister_*` in `on_disable`.
4. Scheduler is started only when `not current_app.config["TESTING"]`.
5. Migrations → `migrations/versions/` + core `alembic.ini` `version_locations`.
   Both `upgrade` + `downgrade`. No raw SQL anywhere (incl. seeds).
6. Line-item handler methods return skip/None/False for line items not owned
   (types not in `{SUBSCRIPTION, ADD_ON}`).
7. Payment plugins must depend on core `ISubscriptionLifecycle`, never on this
   plugin. If you find such an import, it's a bug.

## File map (where to change X)
| Goal | File |
|---|---|
| Plugin wiring / all core registrations | `__init__.py` (`on_enable`/`on_disable`/`register_*`) |
| Runtime defaults | `config.json` + `DEFAULT_CONFIG` in `__init__.py` |
| Admin settings UI schema | `admin-config.json` |
| Activate/refund subscription & add-on; recurring info | `subscription/handlers/line_item_handler.py` |
| Provider-webhook writes (link/renew/cancel/fail) | `subscription/services/subscription_lifecycle.py` |
| Build invoice from a checkout request | `subscription/handlers/checkout_handler.py` |
| Cancellation side-effects | `subscription/handlers/cancel_handler.py` |
| Receipts on activation | `subscription/handlers/subscription_handlers.py` |
| Access-level assign/revoke | `subscription/handlers/access_level_handler.py` |
| What a subscription grants | `subscription/services/subscription_entitlement_provider.py` |
| Read-only queries for core | `subscription/services/{subscription_read_model,catalog_read_model}.py` |
| Plan/period math, token credit rules | `subscription/services/subscription_service.py` |
| Background expiry/dunning | `subscription/scheduler.py` |
| Domain event payloads | `subscription/events.py` |
| HTTP endpoints | `subscription/routes/{user_*,admin_*}.py` |
| ORM models | `subscription/models/*.py` |
| Data access | `subscription/repositories/*.py` |
| Demo/test seeders | `subscription/demo_seed.py` |

## Core seams used (import path → registrar)
- `vbwd.events.line_item_registry` → `ILineItemHandler`, `LineItemContext`,
  `LineItemResult`, `RecurringBillingSpec`; registered via `register_line_item_handlers`.
- `vbwd.events.bus` `event_bus` → publishes/subscribes `subscription.activated`,
  `subscription.cancelled`.
- event dispatcher (`container.event_dispatcher()`) → `checkout.requested`,
  `subscription.cancelled` (+ `emit(SubscriptionCancelledEvent/PaymentFailedEvent)`).
- `vbwd.services.entitlement` → `register_entitlement_provider` / `clear_entitlement_provider`.
- `vbwd.services.subscription_read_model` → `register_subscription_read_model` / `clear_*`.
- `vbwd.services.catalog_read_model` → `register_catalog_read_model` / `clear_*`.
- `vbwd.services.subscription_lifecycle` → `ISubscriptionLifecycle`,
  `register_subscription_lifecycle` / `clear_*`.
- `vbwd.services.deletion_dependency_registry` →
  `register_deletion_dependency_provider("subscription", fn)` / `unregister_*`.
- `vbwd.services.demo_data_registry` → `register_catalog_seeder` /
  `register_test_data_seeder` / `register_test_data_cleaner` / `clear_demo_data_hooks`.
- DI container providers added in `on_enable`: `subscription_repository`,
  `addon_repository`, `addon_subscription_repository`, `tarif_plan_repository`,
  `tarif_plan_category_repository`.

## Line-item handler contract (`SubscriptionLineItemHandler`)
- `HANDLED_TYPES = {LineItemType.SUBSCRIPTION, LineItemType.ADD_ON}`.
- `can_handle_line_item(li, ctx) -> bool`
- `activate_line_item / reverse_line_item / restore_line_item(li, ctx) -> LineItemResult`
- `resolve_catalog_item_id(li) -> str|None` (SUBSCRIPTION→plan id, ADD_ON→addon id)
- `is_recurring_line_item(li) -> bool`
- `recurring_billing_spec(li) -> RecurringBillingSpec(name, billing_period) | None`
- ctor takes the DI `container`; resolves repos via `container.<name>()`.
- activate SUBSCRIPTION side-effects: set ACTIVE + `expires_at`, credit
  `plan.features.default_tokens`, cancel conflicting plans in `is_single`
  categories, publish `subscription.activated`.

## ISubscriptionLifecycle contract (`SubscriptionLifecycle`)
- `link_provider_subscription(invoice_id, provider_subscription_id)`
- `record_provider_renewal(provider, provider_subscription_id, amount, currency,
  provider_reference) -> invoice_id|None` (idempotent on `provider_reference`)
- `cancel_by_provider_subscription_id(provider, provider_subscription_id, reason=None)`
- `mark_provider_payment_failed(provider, provider_subscription_id, error_message)`
- `mark_invoice_payment_failed(invoice_id, provider, error_message, error_code="payment_failed")`

## Permission keys
admin: `subscription.plans.{view,manage}`, `subscription.subscriptions.{view,manage}`,
`subscription.addons.manage`, `subscription.configure`.
user: `subscription.plans.view`, `subscription.manage`,
`subscription.invoices.view`, `subscription.tokens.{view,manage}`.

## Endpoints (blueprint, absolute paths)
Public: `GET /api/v1/tarif-plans`, `GET /api/v1/tarif-plans/<slug_or_id>`,
`GET /api/v1/addons/`, `GET /api/v1/addons/<id>`.
User: `POST /api/v1/user/checkout`; `GET /api/v1/user/subscriptions[/active|/active-all]`;
`GET /api/v1/user/addons`, `GET|POST /api/v1/user/addons/<id>[/cancel]`.
Admin: `/api/v1/admin/tarif-plans/...` (CRUD + activate/archive/copy),
`/api/v1/admin/addons/...` (CRUD + activate/deactivate + `slug/<slug>`),
`/api/v1/admin/subscriptions/...`, plan-category CRUD + attach/detach-plans.

## Tests
- unit: `tests/unit/` (mock repos; line-item handler, entitlement, access level,
  recurring spec, demo seed).
- integration: `tests/integration/` (`db` fixture; DI providers, invoice link,
  trial expiry, repo smoke, schema characterisation `_schema_fingerprint.json`).
- run: `docker compose run --rm test python -m pytest plugins/subscription/tests/ -v`
- gate: `bin/pre-commit-check.sh --full`.

## Gotchas
- `plugins/` is gitignored in the core repo; this plugin is its own git repo.
  Docs/READMEs commit to the plugin repo, not core.
- Enum types (`SubscriptionStatus`, `LineItemType`, `TokenTransactionType`) are in
  core `vbwd.models.enums` — import from there.
- `on_enable` self-heals the `checkout-confirmation` CMS page; tolerate the
  `checkout` plugin being absent.
- Adding a repo without registering its DI provider → runtime
  `'DynamicContainer' object has no attribute '<repo>'` in the checkout/payment path.
