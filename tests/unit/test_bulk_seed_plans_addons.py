"""Unit: S89.1 load-test bulk seed for subscription plans + add-ons (no DB).

Covers the two plugin overrides of the core ``BaseModelExchanger.bulk_seed``
seam:

* ``subscription_plans`` — ``_seed_row`` returns a valid plan (name, price,
  ``BillingPeriod`` enum) + the one shared ``loadtest-`` category slug;
  ``_build_instance`` attaches that one category.
* ``subscription_addons`` — ``_seed_row`` returns a valid FLAT add-on (name,
  price, string ``billing_period``) with no required FK; the base
  ``_build_instance`` builds it directly.

Both seed through the repo's ``bulk_add`` (batched) and are idempotent.

Engineering requirements (binding, restated): TDD-first; SOLID/DI/DRY; Liskov
(overrides preserve the base seed contract); clean code; no overengineering.
Quality guard: ``bin/pre-commit-check.sh --plugin subscription --full``.
"""
from typing import List

from vbwd.models.enums import BillingPeriod

from plugins.subscription.subscription.models.addon import AddOn
from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from plugins.subscription.subscription.models.tarif_plan_category import (
    TarifPlanCategory,
)
from plugins.subscription.subscription.services.data_exchange.subscription_exchangers import (
    _SubscriptionAddonsSeedExchanger,
    _SubscriptionPlansSeedExchanger,
)


class _FakeRepo:
    def __init__(self, existing_keys: List[str] | None = None) -> None:
        self.added: list = []
        self.bulk_calls = 0
        self._existing = list(existing_keys or [])

    def find_natural_keys_with_prefix(self, prefix: str) -> List[str]:
        return [key for key in self._existing if key.startswith(prefix)]

    def bulk_add(self, instances: list) -> None:
        self.bulk_calls += 1
        self.added.extend(instances)

    def add(self, instance) -> None:  # pragma: no cover - fallback
        self.added.append(instance)


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def _plans_exchanger(repo: _FakeRepo) -> _SubscriptionPlansSeedExchanger:
    exchanger = _SubscriptionPlansSeedExchanger(
        entity_key="subscription_plans",
        label="Subscription Plans",
        cluster="sales",
        natural_key="slug",
        model_class=TarifPlan,
        repository=repo,
        session=_FakeSession(),
        public_fields=["slug", "name", "price"],
        view_permission="subscription.plans.view",
        manage_permission="subscription.plans.manage",
        link_field="category_slugs",
        relationship_attr="categories",
        related_model=TarifPlanCategory,
        related_natural_key="slug",
    )
    shared = TarifPlanCategory(slug=exchanger._SEED_CATEGORY_SLUG, name="Load-test")
    exchanger._ensure_seed_prerequisite = lambda: shared
    return exchanger


def _addons_exchanger(repo: _FakeRepo) -> _SubscriptionAddonsSeedExchanger:
    return _SubscriptionAddonsSeedExchanger(
        entity_key="subscription_addons",
        label="Subscription Add-ons",
        cluster="sales",
        natural_key="slug",
        model_class=AddOn,
        repository=repo,
        session=_FakeSession(),
        public_fields=["slug", "name", "price"],
        view_permission="subscription.plans.view",
        manage_permission="subscription.addons.manage",
        link_field="tarif_plan_slugs",
        relationship_attr="tarif_plans",
        related_model=TarifPlan,
        related_natural_key="slug",
    )


def test_plan_seed_row_is_valid_and_categorised() -> None:
    exchanger = _plans_exchanger(_FakeRepo())

    row = exchanger._seed_row(2, "loadtest-subscription_plans-2")

    assert row["slug"] == "loadtest-subscription_plans-2"
    assert row["name"]
    assert row["price"] == _SubscriptionPlansSeedExchanger._SEED_PLAN_PRICE
    assert row["billing_period"] is BillingPeriod.MONTHLY
    assert row["category_slugs"] == [exchanger._SEED_CATEGORY_SLUG]


def test_plan_build_instance_attaches_one_category() -> None:
    exchanger = _plans_exchanger(_FakeRepo())

    plan = exchanger._build_instance(
        exchanger._seed_row(0, "loadtest-subscription_plans-0")
    )

    assert isinstance(plan, TarifPlan)
    assert "category_slugs" not in plan.__dict__
    assert len(plan.categories) == 1


def test_plan_bulk_seed_creates_via_bulk_add() -> None:
    repo = _FakeRepo()
    exchanger = _plans_exchanger(repo)

    result = exchanger.bulk_seed(10, batch_size=4)

    assert result.created == 10
    assert len(repo.added) == 10
    assert repo.bulk_calls >= 1
    assert all(isinstance(item, TarifPlan) for item in repo.added)


def test_plan_bulk_seed_is_idempotent() -> None:
    existing = [f"loadtest-subscription_plans-{index}" for index in range(10)]
    repo = _FakeRepo(existing_keys=existing)
    exchanger = _plans_exchanger(repo)

    result = exchanger.bulk_seed(10)

    assert result.created == 0
    assert result.skipped == 10
    assert repo.added == []


def test_addon_seed_row_is_valid_and_flat() -> None:
    exchanger = _addons_exchanger(_FakeRepo())

    row = exchanger._seed_row(7, "loadtest-subscription_addons-7")

    assert row["slug"] == "loadtest-subscription_addons-7"
    assert row["name"]
    assert row["price"] == _SubscriptionAddonsSeedExchanger._SEED_ADDON_PRICE
    assert row["billing_period"] == BillingPeriod.MONTHLY.value
    # Flat: no required FK / link field on a seeded add-on.
    assert "tarif_plan_slugs" not in row


def test_addon_bulk_seed_creates_via_bulk_add() -> None:
    repo = _FakeRepo()
    exchanger = _addons_exchanger(repo)

    result = exchanger.bulk_seed(10, batch_size=4)

    assert result.created == 10
    assert len(repo.added) == 10
    assert repo.bulk_calls >= 1
    assert all(isinstance(item, AddOn) for item in repo.added)
