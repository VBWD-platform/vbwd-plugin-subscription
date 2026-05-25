# Subscription Plugin (backend)

Subscription billing for the VBWD platform: **tarif plans, plan categories,
add-ons, subscriptions, and checkout**. This is a backend (Python/Flask)
plugin that plugs into the agnostic VBWD core through a set of well-defined
**ports and registries** — the core knows nothing about subscriptions.

> **Core principle:** *VBWD core is agnostic — only plugins are gnostic.*
> Nothing under `vbwd-backend/vbwd/` references a plan, subscription, or add-on.
> This plugin owns every subscription concept and injects itself into core flows
> (checkout, invoices, payments, user deletion, entitlements) via registries.

| | |
|---|---|
| **Plugin id** | `subscription` |
| **Depends on** | `email` plugin (receipts / dunning) |
| **Owns tables** | `vbwd_subscription`, `vbwd_tarif_plan`, `vbwd_tarif_plan_category`, `vbwd_addon`, `vbwd_addon_subscription` |
| **Blueprint** | `subscription_bp` (no url-prefix; routes use absolute `/api/v1/...` paths) |
| **Frontend siblings** | [`vbwd-fe-user/plugins/subscription`](../../../vbwd-fe-user/plugins/subscription), [`vbwd-fe-admin/plugins/subscription-admin`](../../../vbwd-fe-admin/plugins/subscription-admin) |

## Documentation map

| Doc | Audience | Contents |
|---|---|---|
| **README.md** (this file) | everyone | What it is, quick start, layout, key concepts |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | humans | How it works end-to-end: layers, core ports, data model, lifecycle, payments |
| [`docs/EXTENDING.md`](docs/EXTENDING.md) | humans | Recipes: add a feature, a line-item type, an entitlement, an event handler |
| [`docs/LLM_GUIDE.md`](docs/LLM_GUIDE.md) | LLMs / agents | Compact file map, contracts, invariants, "where to change X", gotchas |

## What it does

- **Catalog**: tarif plans (with billing period, price, features, trial), plan
  categories (`is_single` enforces "one plan per category"), and add-ons.
- **Checkout**: turns a chosen plan + token bundles + add-ons into a core
  `UserInvoice` of line items, then activates them when the invoice is paid.
- **Lifecycle**: trials, activation, expiry, cancellation, renewal, dunning —
  driven by a background scheduler and by payment-provider webhooks.
- **Entitlements & access levels**: an active subscription grants the plan's
  entitlements and (via the EventBus) auto-assigns access levels.
- **Recurring billing**: declares which line items are recurring so payment
  providers (stripe/paypal/yookassa) can set up subscriptions generically.

## Quick start

This plugin is enabled on every instance. From `vbwd-backend/`:

```bash
make up                 # start API + Postgres + Redis (plugin auto-enables)
make test-integration   # runs plugin integration tests against real Postgres

# Run just this plugin's tests
docker compose run --rm test python -m pytest plugins/subscription/tests/ -v

# Seed demo catalog + test data (idempotent, service-layer only — never raw SQL)
bash plugins/subscription/bin/populate-db.sh
```

Migrations live **inside the plugin** at
[`migrations/versions/`](migrations/versions) and are registered in the core
`alembic.ini` `version_locations`. Never put subscription migrations in
`vbwd-backend/alembic/`.

## Layout

```
plugins/subscription/
├── __init__.py                 # SubscriptionPlugin — all core wiring lives here
├── config.json                 # runtime defaults (trial_days, intervals, …)
├── admin-config.json           # admin settings UI schema
├── populate_db.py              # demo data entry point (idempotent)
├── bin/                        # populate-db.sh + run_populate.py
├── migrations/versions/        # Alembic migrations (plugin-owned)
├── docs/                       # ← you are here
└── subscription/               # source package (plugin id, not "src")
    ├── models/                 # SQLAlchemy models (extend core BaseModel)
    ├── repositories/           # data access
    ├── services/               # business logic + core port implementations
    ├── handlers/               # event + line-item handlers
    ├── routes/                 # Flask routes on subscription_bp (user + admin)
    ├── events.py               # domain event payloads
    ├── scheduler.py            # background expiry/dunning loop
    ├── demo_seed.py            # catalog + test-data seeders
    └── templates/email/        # receipt / renewal / dunning templates
```

## Key concepts (one paragraph each)

- **Ports & registries.** `__init__.py::on_enable` registers this plugin's
  implementations against core registries: entitlement provider, subscription
  read-model, catalog read-model, **subscription lifecycle** (`ISubscriptionLifecycle`),
  deletion-dependency provider, and demo-data hooks. `register_line_item_handlers`
  adds the **line-item handler**. `on_disable` clears them all. See ARCHITECTURE.

- **Line-item handler** ([`handlers/line_item_handler.py`](subscription/handlers/line_item_handler.py)).
  The single integration point with the core payment/invoice engine. It owns
  `SUBSCRIPTION` and `ADD_ON` line items: activate on payment, reverse on refund,
  restore on refund-reversal, and report `is_recurring` / `recurring_billing_spec`
  so providers can create recurring charges without knowing about subscriptions.

- **Subscription lifecycle port** ([`services/subscription_lifecycle.py`](subscription/services/subscription_lifecycle.py)).
  Implements `ISubscriptionLifecycle` so payment plugins (stripe/paypal/yookassa)
  call generic methods — `link_provider_subscription`, `record_provider_renewal`,
  `cancel_by_provider_subscription_id`, `mark_*_payment_failed` — and never import
  the subscription model or repo.

- **DI providers.** Core declares none of the subscription repositories, so
  `on_enable` registers them on the DI container
  (`container.subscription_repository`, `addon_repository`, …). Handlers and
  other plugins resolve them via the container.

- **Permissions.** `admin_permissions` and `user_permissions` properties declare
  the `subscription.*` permission keys consumed by routes and by both frontends.

## Engineering requirements (binding)

TDD-first · DevOps-first · SOLID · DI · DRY · Liskov · clean code · **no
overengineering** (narrowest change that satisfies the requirement). The quality
gate is `bin/pre-commit-check.sh` — `--full` green on every touched repo means
"done". See `docs/dev_log/.../_engineering-requirements.md` in the SDK root.
