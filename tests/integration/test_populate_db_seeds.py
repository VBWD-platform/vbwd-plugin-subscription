"""Integration: populate_db seeds the subscription plan catalog on install.

Proves the standalone entrypoint (`python plugins/subscription/populate_db.py`)
now actually seeds: `populate()` runs under an app context (the `db` fixture
provides one — the same context the `__main__` block creates via create_app),
writes the demo plans + category, and is idempotent on a second run.
"""
import pytest

from plugins.subscription.populate_db import populate
from plugins.subscription.subscription.models import TarifPlan, TarifPlanCategory

_EXPECTED_PLAN_SLUGS = {"free", "starter", "professional", "enterprise"}


@pytest.fixture(autouse=True)
def _register_optional_plugin_models(db):
    # populate() also seeds the checkout-confirmation CMS page and email
    # templates when those optional plugins are installed. Register their
    # models so create_all() builds the tables; tolerate their absence in
    # isolated plugin CI (each populate path guards its own import).
    try:
        import plugins.cms.src.models  # noqa: F401
    except ImportError:
        pass
    try:
        import plugins.email.src.models.email_template  # noqa: F401
    except ImportError:
        pass

    db.create_all()


def test_populate_seeds_plans_and_category(db):
    populate()

    plan_slugs = {plan.slug for plan in db.session.query(TarifPlan).all()}
    assert _EXPECTED_PLAN_SLUGS <= plan_slugs
    category = (
        db.session.query(TarifPlanCategory).filter_by(slug="subscription-plans").first()
    )
    assert category is not None


def test_populate_is_idempotent(db):
    populate()
    first_count = db.session.query(TarifPlan).count()

    populate()
    assert db.session.query(TarifPlan).count() == first_count
