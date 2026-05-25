# Subscription Plugin — Extending (backend)

Practical recipes for changing this plugin **without touching core**. Read
[`ARCHITECTURE.md`](ARCHITECTURE.md) first for the seams these recipes use.

Golden rules:

1. **Never edit `vbwd-backend/vbwd/`** for subscription behaviour. If core seems
   to be missing a seam, add a generic registry/port in core and implement it
   here — don't hardcode a subscription concept into core.
2. **TDD-first.** Add/extend a test in `tests/` before the change.
3. **Mirror every `register_*` with a `clear_*`/`unregister_*`** in `on_disable`.
4. **Migrations live in `migrations/versions/`** (plugin), registered in core
   `alembic.ini` `version_locations`. No raw SQL, ever.
5. Run `bin/pre-commit-check.sh --quick` while iterating, `--full` before done.

---

## Recipe: add a field to a plan / add-on

1. Add the column to the model in `subscription/models/`.
2. `flask db migrate` then move the generated file into
   `plugins/subscription/migrations/versions/` (do **not** leave it in core).
   Implement both `upgrade()` and `downgrade()`.
3. Expose it in the model's `to_dict()` (explicit fields + `isoformat()` for
   timestamps).
4. Accept/return it in the relevant route(s) under `subscription/routes/`.
5. Surface it in the frontends (fe-admin form, fe-user view) — see their docs.
6. Update the integration **schema characterisation** test (the table-shape
   fingerprint) so the guard reflects the new column.

## Recipe: add a new line-item type this plugin owns

The line-item handler is the bridge to the core payment/invoice engine.

1. Add the type to the core `LineItemType` enum **only if** it's a genuinely new,
   generic invoice concept (prefer reusing `SUBSCRIPTION`/`ADD_ON`).
2. In `handlers/line_item_handler.py`:
   - add it to `HANDLED_TYPES`;
   - branch it in `activate_line_item` / `reverse_line_item` / `restore_line_item`;
   - return the right value from `resolve_catalog_item_id`,
     `is_recurring_line_item`, and `recurring_billing_spec`.
3. Keep activation/reversal **symmetric** (whatever activate credits, reverse
   debits) so refunds are clean.
4. Unit-test it in `tests/unit/test_line_item_handler.py`.

> Each method must return `LineItemResult.skip()` / `None` / `False` for line
> items this plugin does **not** own — never assume every line item is yours.

## Recipe: make a subscription do something on activation

Activation already publishes `subscription.activated` on the EventBus.

- **In this plugin:** add a handler in `subscription/handlers/`, subscribe to it
  from `register_event_handlers(event_bus)` in `__init__.py` (and add the matching
  unsubscribe semantics if needed).
- **In another plugin:** that plugin subscribes to `subscription.activated` itself
  — this plugin doesn't need to know about it. That's the point of the EventBus.

## Recipe: integrate a new payment provider

Don't import this plugin from the provider. Instead, in the provider's webhook
handler, call the core port:

```python
from vbwd.services.subscription_lifecycle import get_subscription_lifecycle

lifecycle = get_subscription_lifecycle()      # returns our SubscriptionLifecycle
if lifecycle:
    lifecycle.link_provider_subscription(invoice_id, provider_subscription_id)
    # …record_provider_renewal / cancel_by_provider_subscription_id / mark_*_failed
```

For recurring setup, the provider reads `is_recurring_line_item` /
`recurring_billing_spec` from the line-item registry — also generic. The provider
stays subscription-agnostic; this plugin owns all the writes.

## Recipe: add an entitlement / change what a plan grants

Edit `services/subscription_entitlement_provider.py`. Core asks the registered
entitlement provider "what does this user get?"; this is where plan `features`
become feature flags / token grants. Unit-test in
`tests/unit/test_subscription_entitlement_provider.py`.

## Recipe: add a new API endpoint

1. Pick/extend a module in `subscription/routes/` (`user_*` or `admin_*`).
2. `from plugins.subscription.subscription.routes import subscription_bp` and
   decorate with the **absolute** path: `@subscription_bp.route("/api/v1/...")`
   (the blueprint has no url-prefix).
3. Gate it with the right `subscription.*` permission.
4. Instantiate services in a factory using `db.session` (or resolve repos from
   `current_app.container`).
5. If a new repo is needed, register it as a DI provider in `on_enable` and clear
   it in `on_disable`.

## Recipe: change demo / test seed data

Edit `subscription/demo_seed.py` (`seed_catalog`, `seed_test_data`,
`clean_test_data`). These are registered with the core demo-data registry in
`on_enable`. Keep them **idempotent** and **service-layer only** — no direct DB
writes. `bin/populate-db.sh` runs them; tests rely on a clean cold start.

## Recipe: add a config option

1. Add the key + default to `DEFAULT_CONFIG` (in `__init__.py`) **and**
   `config.json`.
2. Add a field for it in `admin-config.json` (correct tab, component, validation).
3. Read it via `self.config.get("your_key", default)`.

## Checklist before "done"

- [ ] Test added/updated and green (`--full`).
- [ ] Migration in `plugins/.../migrations/versions/`, up + down validated.
- [ ] `on_disable` clears anything new that `on_enable` registered.
- [ ] No core file touched; no raw SQL; no `# noqa`/`# type: ignore` without
      explicit approval.
- [ ] `bin/pre-commit-check.sh --full` green.
