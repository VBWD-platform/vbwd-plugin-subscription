"""Subscription entity exchangers for the S46 data-exchange seam (S46.6).

Exposes the subscription plugin's catalog + records through the core
``EntityExchanger`` contract so they appear on the generic Settings →
Import/Export page and the per-list controls — coexisting with the existing
subscription admin routes (DRY; no parallel serialisation).

Entities:

* ``subscription_plans`` (``TarifPlan``, natural key ``slug``) — import+export.
* ``subscription_addons`` (``AddOn``, natural key ``slug``) — import+export.
* ``subscriptions`` (``Subscription``, natural key ``id``) — **export-only**:
  a subscription record binds a concrete user + plan + invoice line item via
  UUID FKs and a lifecycle the billing engine owns, so it is not structurally
  importable. Per Liskov it raises :class:`UnsupportedOperationError` from
  ``import_`` rather than silently failing.

Design notes:

* **Reused perms** — the plugin already ships ``subscription.plans.*`` /
  ``subscription.subscriptions.*`` / ``subscription.addons.manage``. Each
  exchanger maps ``export_permission`` / ``import_permission`` onto those rather
  than minting a parallel ``<entity>.export`` family (single source of truth).
* **DRY** — the catalog exchangers reuse :class:`BaseModelExchanger`; only the
  narrow ``_SessionModelRepository`` adapter is added (mirrors core's and CMS's),
  because the existing subscription repos expose paginated/domain finders, not
  the four flat methods the base exchanger needs (ISP).
* **No core change** — registration happens in ``SubscriptionPlugin.on_enable``
  through the shared ``db.session``; core imports no ``plugins.*`` module.

Engineering requirements (binding, restated): TDD-first; DevOps-first (cold
local + CI via the shared ``db`` fixture, no raw SQL); SOLID (one exchanger per
entity, narrow ports); DI (session injected); DRY (delegate to the base
exchanger / existing models); Liskov (export-only raises); clean code; no
overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin subscription
--full``.
"""
from typing import Any, List, Optional

from vbwd.services.data_exchange.base_model_exchanger import BaseModelExchanger
from vbwd.services.data_exchange.port import (
    CLUSTER_SALES,
    EntityExchanger,
    Envelope,
    ExportSelector,
    ImportResult,
    UnsupportedOperationError,
)
from vbwd.services.data_exchange.registry import data_exchange_registry

# Existing subscription permissions (single source — declared in
# SubscriptionPlugin.admin_permissions).
PERM_PLANS_VIEW = "subscription.plans.view"
PERM_PLANS_MANAGE = "subscription.plans.manage"
PERM_ADDONS_MANAGE = "subscription.addons.manage"
PERM_SUBSCRIPTIONS_VIEW = "subscription.subscriptions.view"


class _SessionModelRepository:
    """Narrow model repo satisfying the ``BaseModelExchanger`` contract.

    Mirrors core's ``core_exchangers._SessionModelRepository`` and CMS's: the
    subscription repos expose paginated / domain finders rather than the four
    flat methods the base exchanger needs, so this adapter provides exactly
    those (ISP) without touching the existing repos.
    """

    def __init__(self, session: Any, model_class: type, natural_key: str) -> None:
        self._session = session
        self._model_class = model_class
        self._natural_key = natural_key

    def find_all(self) -> List[Any]:
        return self._session.query(self._model_class).all()

    def find_by_natural_key(self, value: Any) -> Optional[Any]:
        column = getattr(self._model_class, self._natural_key)
        return self._session.query(self._model_class).filter(column == value).first()

    def add(self, instance: Any) -> None:
        self._session.add(instance)

    def delete_all(self) -> None:
        self._session.query(self._model_class).delete()


class _PermissionMappedModelExchanger(BaseModelExchanger):
    """A ``BaseModelExchanger`` whose perms map onto existing subscription perms.

    The generic registry gates non-settings clusters by ``export_permission`` /
    ``import_permission``; this subclass returns the existing subscription
    permission so the gate reuses subscription RBAC (no parallel perm family).
    """

    def __init__(
        self,
        *,
        view_permission: str,
        manage_permission: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._view_permission = view_permission
        self._manage_permission = manage_permission

    @property
    def export_permission(self) -> str:
        return self._view_permission

    @property
    def import_permission(self) -> str:
        return self._manage_permission


class SubscriptionsExchanger(EntityExchanger):
    """``Subscription`` records, keyed by ``id`` — export-only.

    A subscription binds a user, a plan and an invoice line item via UUID FKs
    and a lifecycle the billing engine owns; it is not a portable catalog row,
    so it is export-only. ``import_`` raises (Liskov) rather than pretending.
    """

    entity_key = "subscriptions"
    label = "Subscriptions"
    cluster = CLUSTER_SALES
    natural_key = "id"
    supports_export = True
    supports_import = False
    supported_formats = frozenset({"json"})
    secret_fields = frozenset({"provider_subscription_id"})
    pii_fields = frozenset({"user_id"})

    _ROW_FIELDS = (
        "id",
        "user_id",
        "tarif_plan_id",
        "pending_plan_id",
        "status",
        "started_at",
        "expires_at",
        "trial_end_at",
        "cancelled_at",
        "paused_at",
        "payment_failed_at",
    )

    def __init__(self, session: Any) -> None:
        self._session = session

    def export(self, selector: ExportSelector, *, include_pii: bool) -> Envelope:
        from plugins.subscription.subscription.models.subscription import Subscription

        rows = self._session.query(Subscription).all()
        if selector.ids:
            wanted = {str(value) for value in selector.ids}
            rows = [row for row in rows if str(row.id) in wanted]
        serialised = [self._serialise(row, include_pii=include_pii) for row in rows]
        return Envelope(entity_key=self.entity_key, rows=serialised)

    def _serialise(self, row: Any, *, include_pii: bool) -> dict:
        result: dict = {}
        for field_name in self._ROW_FIELDS:
            if field_name in self.secret_fields:
                continue
            value = getattr(row, field_name)
            if field_name in self.pii_fields and not include_pii:
                value = None
            result[field_name] = value
        return result

    def import_(self, payload: dict, *, mode: str, dry_run: bool) -> ImportResult:
        raise UnsupportedOperationError(
            "subscriptions are export-only: a subscription record is owned by the "
            "billing engine and cannot be imported"
        )

    @property
    def export_permission(self) -> str:
        return PERM_SUBSCRIPTIONS_VIEW


def build_subscription_exchangers(session: Any) -> List[EntityExchanger]:
    """Construct the subscription exchangers bound to ``session``."""
    from plugins.subscription.subscription.models.addon import AddOn
    from plugins.subscription.subscription.models.tarif_plan import TarifPlan

    return [
        SubscriptionsExchanger(session),
        _PermissionMappedModelExchanger(
            entity_key="subscription_plans",
            label="Subscription Plans",
            cluster=CLUSTER_SALES,
            natural_key="slug",
            model_class=TarifPlan,
            repository=_SessionModelRepository(session, TarifPlan, "slug"),
            session=session,
            public_fields=[
                "slug",
                "name",
                "description",
                "price_float",
                "currency",
                "billing_period",
                "features",
                "trial_days",
                "is_active",
                "sort_order",
            ],
            view_permission=PERM_PLANS_VIEW,
            manage_permission=PERM_PLANS_MANAGE,
        ),
        _PermissionMappedModelExchanger(
            entity_key="subscription_addons",
            label="Subscription Add-ons",
            cluster=CLUSTER_SALES,
            natural_key="slug",
            model_class=AddOn,
            repository=_SessionModelRepository(session, AddOn, "slug"),
            session=session,
            public_fields=[
                "slug",
                "name",
                "description",
                "price",
                "currency",
                "billing_period",
                "config",
                "is_active",
                "sort_order",
            ],
            view_permission=PERM_PLANS_VIEW,
            manage_permission=PERM_ADDONS_MANAGE,
        ),
    ]


def register_subscription_exchangers(session: Any) -> None:
    """Register the subscription exchangers into the registry (idempotent).

    Called from ``SubscriptionPlugin.on_enable``. Re-registering replaces by
    key, so a repeat enable (per-test app) is clear-safe.
    """
    for exchanger in build_subscription_exchangers(session):
        data_exchange_registry.register(exchanger)
