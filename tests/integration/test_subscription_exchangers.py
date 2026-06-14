"""Integration: subscription entity exchangers (real PG) — S46.6.

* ``subscription_plans`` / ``subscription_addons`` round-trip by ``slug``
  (export → wipe → import → equal).
* ``subscriptions`` is export-only: ``import_`` raises
  ``UnsupportedOperationError`` (Liskov); its export strips the provider secret
  and redacts the ``user_id`` PII unless ``include_pii``.
* registration: after ``SubscriptionPlugin._register_data_exchangers`` the
  exchangers appear in ``data_exchange_registry`` with cluster ``sales``.

Data is seeded through the ORM session (no raw SQL); the shared ``db`` fixture
creates + drops the test DB.

Engineering requirements (binding, restated): TDD-first; DevOps-first; SOLID/DI/
DRY; Liskov (export-only raises); no overengineering. Quality guard:
``bin/pre-commit-check.sh --plugin subscription --full``.
"""
import uuid

import pytest

from vbwd.services.data_exchange.envelope import build_envelope, rows_to_csv
from vbwd.services.data_exchange.port import (
    CLUSTER_SALES,
    ExportSelector,
    UnsupportedOperationError,
)
from plugins.subscription.subscription.models.addon import AddOn
from plugins.subscription.subscription.models.subscription import (
    Subscription,
    SubscriptionStatus,
)
from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from vbwd.models.enums import BillingPeriod
from plugins.subscription.subscription.services.data_exchange.subscription_exchangers import (  # noqa: E501
    build_subscription_exchangers,
)


def _exchangers(session):
    return {
        exchanger.entity_key: exchanger
        for exchanger in build_subscription_exchangers(session)
    }


class TestPlansRoundTrip:
    def test_round_trip_by_slug(self, db):
        slug = f"plan-{uuid.uuid4().hex[:8]}"
        db.session.add(
            TarifPlan(
                slug=slug,
                name="Gold",
                description="Gold plan",
                price=19.0,
                billing_period=BillingPeriod.MONTHLY,
                trial_days=7,
            )
        )
        db.session.commit()

        exchanger = _exchangers(db.session)["subscription_plans"]
        before = exchanger.export(ExportSelector(ids=[slug]), include_pii=False).rows
        assert before and before[0]["slug"] == slug
        assert before[0]["name"] == "Gold"

        db.session.query(TarifPlan).filter(TarifPlan.slug == slug).delete()
        db.session.commit()
        assert (
            db.session.query(TarifPlan).filter(TarifPlan.slug == slug).first() is None
        )

        payload = build_envelope("subscription_plans", before, instance="test")
        result = exchanger.import_(payload, mode="upsert", dry_run=False)
        assert result.created == 1

        rebuilt = db.session.query(TarifPlan).filter(TarifPlan.slug == slug).first()
        assert rebuilt is not None
        assert rebuilt.name == "Gold"
        assert rebuilt.trial_days == 7


class TestAddonsRoundTrip:
    def test_round_trip_by_slug(self, db):
        slug = f"addon-{uuid.uuid4().hex[:8]}"
        db.session.add(
            AddOn(slug=slug, name="Extra Seats", description="More seats", price=5)
        )
        db.session.commit()

        exchanger = _exchangers(db.session)["subscription_addons"]
        before = exchanger.export(ExportSelector(ids=[slug]), include_pii=False).rows
        assert before and before[0]["slug"] == slug

        db.session.query(AddOn).filter(AddOn.slug == slug).delete()
        db.session.commit()

        payload = build_envelope("subscription_addons", before, instance="test")
        exchanger.import_(payload, mode="upsert", dry_run=False)
        rebuilt = db.session.query(AddOn).filter(AddOn.slug == slug).first()
        assert rebuilt is not None
        assert rebuilt.name == "Extra Seats"


class TestSubscriptionsExportOnly:
    def _seed_subscription(self, db):
        from vbwd.models.user import User

        plan = TarifPlan(
            slug=f"sp-{uuid.uuid4().hex[:8]}",
            name="P",
            price=1.0,
            billing_period=BillingPeriod.MONTHLY,
        )
        user = User(
            email=f"u-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x",
        )
        db.session.add_all([plan, user])
        db.session.commit()
        sub = Subscription(
            user_id=user.id,
            tarif_plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            provider_subscription_id=f"prov-{uuid.uuid4().hex[:8]}",
        )
        db.session.add(sub)
        db.session.commit()
        return sub

    def test_import_raises_unsupported(self, db):
        exchanger = _exchangers(db.session)["subscriptions"]
        payload = build_envelope("subscriptions", [], instance="test")
        with pytest.raises(UnsupportedOperationError):
            exchanger.import_(payload, mode="upsert", dry_run=False)

    def test_export_strips_secret_and_redacts_pii(self, db):
        sub = self._seed_subscription(db)
        exchanger = _exchangers(db.session)["subscriptions"]

        without_pii = exchanger.export(
            ExportSelector(ids=[str(sub.id)]), include_pii=False
        ).rows
        assert without_pii and "provider_subscription_id" not in without_pii[0]
        assert without_pii[0]["user_id"] is None

        with_pii = exchanger.export(
            ExportSelector(ids=[str(sub.id)]), include_pii=True
        ).rows
        assert with_pii[0]["user_id"] == sub.user_id


class TestCsvExport:
    """Sales entities list ``csv`` and CSV-export through ``rows_to_csv``."""

    def test_subscription_plans_csv_export(self, db):
        slug = f"plan-{uuid.uuid4().hex[:8]}"
        db.session.add(
            TarifPlan(
                slug=slug,
                name="Gold",
                price=19.0,
                billing_period=BillingPeriod.MONTHLY,
            )
        )
        db.session.commit()
        exchanger = _exchangers(db.session)["subscription_plans"]
        assert "csv" in exchanger.supported_formats
        rows = exchanger.export(ExportSelector(ids=[slug]), include_pii=False).rows
        csv_text = rows_to_csv(rows)
        assert "slug" in csv_text.splitlines()[0]
        assert slug in csv_text

    def test_subscriptions_csv_export(self, db):
        sub = TestSubscriptionsExportOnly()._seed_subscription(db)
        exchanger = _exchangers(db.session)["subscriptions"]
        assert "csv" in exchanger.supported_formats
        rows = exchanger.export(
            ExportSelector(ids=[str(sub.id)]), include_pii=True
        ).rows
        csv_text = rows_to_csv(rows)
        assert csv_text.splitlines()[0]  # non-empty header row
        assert len(csv_text.splitlines()) >= 2


class TestRegistration:
    def test_on_enable_registers_subscription_exchangers(self, db):
        from vbwd.services.data_exchange.registry import data_exchange_registry
        from plugins.subscription import SubscriptionPlugin

        plugin = SubscriptionPlugin()
        plugin.initialize({})
        plugin._register_data_exchangers()

        by_key = {
            exchanger.entity_key: exchanger
            for exchanger in data_exchange_registry.all()
        }
        for key in ("subscription_plans", "subscription_addons", "subscriptions"):
            assert key in by_key
            assert by_key[key].cluster == CLUSTER_SALES
        assert by_key["subscriptions"].supports_import is False
