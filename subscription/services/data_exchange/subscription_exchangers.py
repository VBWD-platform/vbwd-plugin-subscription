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
from enum import Enum
from typing import Any, List, Optional

from vbwd.models.enums import BillingPeriod
from vbwd.services.data_exchange.base_model_exchanger import (
    LOADTEST_SLUG_PREFIX,
    BaseModelExchanger,
)
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

    # ── heavy-load scale hooks (S89.1) ────────────────────────────────────
    # The base exchanger calls these via ``getattr`` when present so a 100k
    # seed/export is O(batches), not O(N²). Absent → it falls back to full
    # ``find_all`` scans (fine for tiny tables, too slow at load-test scale).

    def iter_rows(self, batch_size: int) -> Any:
        """Yield rows in ``yield_per`` pages (bounded memory)."""
        return (
            self._session.query(self._model_class)
            .yield_per(batch_size)
            .enable_eagerloads(False)
        )

    def bulk_add(self, instances: List[Any]) -> None:
        """Insert a batch through the unit of work (one flush per batch).

        Uses ``add_all`` + ``flush`` rather than ``bulk_save_objects`` because a
        seeded plan carries an M2M ``categories`` link that
        ``bulk_save_objects`` would silently skip (it bypasses relationship
        cascades). ``add_all`` keeps the batch a single flush — still
        O(batches) — while persisting the association rows. The caller commits.
        """
        self._session.add_all(instances)
        self._session.flush()

    def find_natural_keys_with_prefix(self, prefix: str) -> List[str]:
        """Return the natural-key values that start with ``prefix`` (idempotency)."""
        column = getattr(self._model_class, self._natural_key)
        rows = self._session.query(column).filter(column.like(f"{prefix}%")).all()
        return [row[0] for row in rows]

    def delete_natural_keys_with_prefix(self, prefix: str) -> int:
        """Delete every row whose natural key starts with ``prefix``. Returns count.

        Scoped to this model and the ``loadtest-`` prefix only, so it never
        touches real/demo data. ``synchronize_session=False`` keeps it a single
        statement (the caller commits the session).
        """
        column = getattr(self._model_class, self._natural_key)
        return (
            self._session.query(self._model_class)
            .filter(column.like(f"{prefix}%"))
            .delete(synchronize_session=False)
        )


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


class _CategoryExchanger(_PermissionMappedModelExchanger):
    """``TarifPlanCategory`` exchanger carrying the self-referential parent.

    The category tree references itself by ``parent_id`` (a local UUID).
    ``fk_natural_key_map`` is export-only, so this subclass exports the parent's
    ``slug`` as ``parent_slug`` and — because the base import writes row values
    straight onto the model — resolves that slug back to the local parent id on
    import (skip-with-error if the parent is absent, never crash — Liskov).
    """

    PARENT_SLUG_FIELD = "parent_slug"

    def _serialise_row(self, row: Any, *, include_pii: bool) -> dict:
        serialised = super()._serialise_row(row, include_pii=include_pii)
        parent = getattr(row, "parent", None)
        serialised[self.PARENT_SLUG_FIELD] = parent.slug if parent is not None else None
        return serialised

    def _import_row(
        self, row: dict, index: int, result: ImportResult, *, dry_run: bool
    ) -> None:
        parent_slug = row.pop(self.PARENT_SLUG_FIELD, None)
        parent_id = None
        if parent_slug:
            parent = self._repository.find_by_natural_key(parent_slug)
            if parent is None:
                result.errors.append(
                    {
                        "row": index,
                        "reason": f"unknown parent category slug '{parent_slug}'",
                    }
                )
                return
            parent_id = parent.id
        row = {**row, "parent_id": parent_id}
        super()._import_row(row, index, result, dry_run=dry_run)


class _M2MSlugExchanger(_PermissionMappedModelExchanger):
    """A ``BaseModelExchanger`` carrying one M2M relationship by referent slug.

    The base export writes only scalar columns and the base import writes row
    values straight onto the model, so an M2M link cannot travel through
    ``fk_natural_key_map``. This subclass serialises the related rows' slugs into
    a list field on export and, on import, resolves each slug to a local row and
    assigns the relationship (skip-with-error on an unknown slug — Liskov).
    """

    def __init__(
        self,
        *,
        link_field: str,
        relationship_attr: str,
        related_model: type,
        related_natural_key: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._link_field = link_field
        self._relationship_attr = relationship_attr
        self._related_model = related_model
        self._related_natural_key = related_natural_key

    def _serialise_row(self, row: Any, *, include_pii: bool) -> dict:
        serialised = super()._serialise_row(row, include_pii=include_pii)
        related = getattr(row, self._relationship_attr)
        serialised[self._link_field] = [
            getattr(item, self._related_natural_key) for item in related
        ]
        return serialised

    def _resolve_related(
        self, slugs: List[Any], index: int, result: ImportResult
    ) -> Optional[List[Any]]:
        resolved: List[Any] = []
        for slug in slugs:
            column = getattr(self._related_model, self._related_natural_key)
            related = (
                self._session.query(self._related_model).filter(column == slug).first()
            )
            if related is None:
                result.errors.append(
                    {
                        "row": index,
                        "reason": (
                            f"unknown {self._link_field} '{slug}' for "
                            f"{self.entity_key}"
                        ),
                    }
                )
                return None
            resolved.append(related)
        return resolved

    def _import_row(
        self, row: dict, index: int, result: ImportResult, *, dry_run: bool
    ) -> None:
        slugs = row.pop(self._link_field, None) or []
        related = self._resolve_related(slugs, index, result)
        if related is None:
            return
        created_before = result.created
        updated_before = result.updated
        super()._import_row(row, index, result, dry_run=dry_run)
        if dry_run:
            return
        applied = result.created > created_before or result.updated > updated_before
        if applied:
            instance = self._repository.find_by_natural_key(row.get(self.natural_key))
            if instance is not None:
                setattr(instance, self._relationship_attr, related)


class _SubscriptionPlansSeedExchanger(_M2MSlugExchanger):
    """``subscription_plans`` exchanger + S89.1 load-test seed support.

    A synthetic plan needs a non-null ``name``, ``billing_period`` and a valid
    ``price``, and links the one shared ``loadtest-`` plan category (so 100k
    plans sit in one category — cheap to reset). The base ``bulk_seed`` loop
    builds each instance via ``_build_instance``; ``_M2MSlugExchanger`` pops the
    ``category_slugs`` link in ``_import_row`` before reaching
    ``_build_instance``, so popping + attaching the category here is seed-only
    and import-safe.
    """

    _SEED_PLAN_PRICE = 19.0
    _SEED_CATEGORY_SLUG = f"{LOADTEST_SLUG_PREFIX}subscription_plans-cat"
    _SEED_CATEGORY_NAME = "Load-test plans"

    # Cache of the one shared ``loadtest-`` category; ``None`` until the first
    # seeded row creates/looks it up. Declared so mypy sees the attr.
    _seed_category: Optional[Any] = None

    def _seed_row(self, index: int, natural_value: str) -> dict:
        return {
            "slug": natural_value,
            "name": f"Load-test plan {index}",
            "description": f"Synthetic load-test plan {index}",
            "price": self._SEED_PLAN_PRICE,
            "billing_period": BillingPeriod.MONTHLY,
            "features": [],
            "trial_days": 0,
            "is_active": True,
            "sort_order": index,
            "category_slugs": [self._SEED_CATEGORY_SLUG],
        }

    def _resolve_related(
        self, slugs: List[Any], index: int, result: ImportResult
    ) -> Optional[List[Any]]:
        """Resolve the linked category slugs, self-healing the seed prerequisite.

        The S89 bench resets the one shared ``loadtest-`` plan category before
        each ``import:cold``; the exported envelope still references it, so when
        that slug alone is missing we recreate it via ``_ensure_seed_prerequisite``
        instead of skipping. Any OTHER unknown slug still skips-with-error via the
        base resolver — never invent data for a typo (Liskov).
        """
        if self._SEED_CATEGORY_SLUG in slugs:
            column = getattr(self._related_model, self._related_natural_key)
            existing = (
                self._session.query(self._related_model)
                .filter(column == self._SEED_CATEGORY_SLUG)
                .first()
            )
            if existing is None:
                self._ensure_seed_prerequisite()
        return super()._resolve_related(slugs, index, result)

    def _build_instance(self, row: dict) -> Any:
        """Build a ``TarifPlan``; attach the shared category ONLY on the seed path.

        ``bulk_seed`` leaves ``category_slugs`` in the row (set by ``_seed_row``);
        import pops it in ``_M2MSlugExchanger._import_row`` before this runs. So
        the presence of ``category_slugs`` distinguishes seed from import — on
        import we defer to the base (the M2M is reapplied by ``_import_row``) and
        never spawn the load-test category.
        """
        if "category_slugs" not in row:
            return super()._build_instance(row)
        prepared = dict(row)
        prepared.pop("category_slugs", None)
        plan = self._model_class(**prepared)
        plan.categories = [self._ensure_seed_prerequisite()]
        return plan

    def _ensure_seed_prerequisite(self) -> Any:
        """Return the one shared ``loadtest-`` plan category, creating it once.

        Created + committed through the existing
        ``TarifPlanCategoryRepository`` (no raw SQL) and cached so 100k plans
        share one category. Idempotent — an existing category is reused. A cached
        category that has since been deleted (e.g. a reset on a sibling
        exchanger) is dropped and re-created, so the cache never returns a stale
        referent.
        """
        if self._seed_category is not None and not self._is_deleted(
            self._seed_category
        ):
            return self._seed_category
        from plugins.subscription.subscription.models.tarif_plan_category import (
            TarifPlanCategory,
        )
        from plugins.subscription.subscription.repositories.tarif_plan_category_repository import (
            TarifPlanCategoryRepository,
        )

        repository = TarifPlanCategoryRepository(self._session)
        category = repository.find_by_slug(self._SEED_CATEGORY_SLUG)
        if category is None:
            category = TarifPlanCategory(
                slug=self._SEED_CATEGORY_SLUG,
                name=self._SEED_CATEGORY_NAME,
                description="Shared category for load-test plans (S89.1).",
            )
            repository.save(category)
        self._seed_category = category
        return category

    @staticmethod
    def _is_deleted(instance: Any) -> bool:
        """True when ``instance`` has been deleted/detached from its session.

        Guards the cache: a sibling exchanger's reset can delete the cached
        category out from under this instance, leaving it deleted/detached.
        """
        from sqlalchemy import inspect as sqlalchemy_inspect

        state = sqlalchemy_inspect(instance)
        return state.deleted or state.detached

    def _reset_loadtest_rows(self) -> int:
        deleted = super()._reset_loadtest_rows()
        self._drop_orphaned_seed_category()
        self._seed_category = None
        return deleted

    def _drop_orphaned_seed_category(self) -> None:
        from plugins.subscription.subscription.models.tarif_plan import TarifPlan
        from plugins.subscription.subscription.models.tarif_plan_category import (
            TarifPlanCategory,
        )

        category = (
            self._session.query(TarifPlanCategory)
            .filter(TarifPlanCategory.slug == self._SEED_CATEGORY_SLUG)
            .first()
        )
        if category is None:
            return
        # Query the DB for any plan still in this category rather than reading the
        # (possibly stale) relationship: the prefix delete ran with
        # ``synchronize_session=False`` so the loaded collection may be stale.
        still_referenced = (
            self._session.query(TarifPlan.id)
            .filter(TarifPlan.categories.any(TarifPlanCategory.id == category.id))
            .first()
        )
        if still_referenced is None:
            self._session.delete(category)


class _SubscriptionAddonsSeedExchanger(_M2MSlugExchanger):
    """``subscription_addons`` exchanger + S89.1 load-test seed support.

    Add-ons carry no required FK (the ``tarif_plans`` M2M is optional — an
    add-on with no plans is "independent / visible to all"), so a seeded add-on
    is a flat row: a valid ``name``, ``price`` and string ``billing_period``.
    No prerequisite is created; the base ``_build_instance`` builds it directly.
    """

    _SEED_ADDON_PRICE = 4.99

    def _seed_row(self, index: int, natural_value: str) -> dict:
        return {
            "slug": natural_value,
            "name": f"Load-test add-on {index}",
            "description": f"Synthetic load-test add-on {index}",
            "price": self._SEED_ADDON_PRICE,
            "billing_period": BillingPeriod.MONTHLY.value,
            "config": {},
            "is_active": True,
            "sort_order": index,
        }


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
    supported_formats = frozenset({"json", "csv"})
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
            elif isinstance(value, Enum):
                value = value.value
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
    from plugins.subscription.subscription.models.tarif_plan_category import (
        TarifPlanCategory,
    )

    return [
        SubscriptionsExchanger(session),
        _CategoryExchanger(
            entity_key="subscription_categories",
            label="Plan Categories",
            cluster=CLUSTER_SALES,
            natural_key="slug",
            model_class=TarifPlanCategory,
            repository=_SessionModelRepository(session, TarifPlanCategory, "slug"),
            session=session,
            public_fields=[
                "slug",
                "name",
                "description",
                "is_single",
                "sort_order",
            ],
            supported_formats=frozenset({"json", "csv"}),
            view_permission=PERM_PLANS_VIEW,
            manage_permission=PERM_PLANS_MANAGE,
        ),
        _SubscriptionPlansSeedExchanger(
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
                "price",
                "billing_period",
                "features",
                "trial_days",
                "is_active",
                "sort_order",
            ],
            supported_formats=frozenset({"json", "csv"}),
            view_permission=PERM_PLANS_VIEW,
            manage_permission=PERM_PLANS_MANAGE,
            link_field="category_slugs",
            relationship_attr="categories",
            related_model=TarifPlanCategory,
            related_natural_key="slug",
        ),
        _SubscriptionAddonsSeedExchanger(
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
                "billing_period",
                "config",
                "is_active",
                "sort_order",
            ],
            supported_formats=frozenset({"json", "csv"}),
            view_permission=PERM_PLANS_VIEW,
            manage_permission=PERM_ADDONS_MANAGE,
            link_field="tarif_plan_slugs",
            relationship_attr="tarif_plans",
            related_model=TarifPlan,
            related_natural_key="slug",
        ),
    ]


def register_subscription_exchangers(session: Any) -> None:
    """Register the subscription exchangers into the registry (idempotent).

    Called from ``SubscriptionPlugin.on_enable``. Re-registering replaces by
    key, so a repeat enable (per-test app) is clear-safe.
    """
    for exchanger in build_subscription_exchangers(session):
        data_exchange_registry.register(exchanger)
