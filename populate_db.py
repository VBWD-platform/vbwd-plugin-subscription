"""Subscription plugin demo data — idempotent seeder.

Creates demo tarif plans, categories, and sample subscriptions.
Run via: flask populate-subscription
"""
import logging

from vbwd.extensions import db

logger = logging.getLogger(__name__)


def populate(app=None):
    """Populate subscription demo data (idempotent)."""
    from plugins.subscription.subscription.models import TarifPlan, TarifPlanCategory, AddOn

    # Category
    category = db.session.query(TarifPlanCategory).filter_by(
        slug="subscription-plans"
    ).first()
    if not category:
        from uuid import uuid4

        category = TarifPlanCategory(
            id=uuid4(),
            name="Subscription Plans",
            slug="subscription-plans",
            description="Default subscription category",
            is_single=True,
        )
        db.session.add(category)
        db.session.flush()
        logger.info("[subscription] Created category: subscription-plans")

    # Plans
    plans_data = [
        {
            "name": "Free",
            "slug": "free",
            "description": "Get started for free",
            "price_float": 0.0,
            "billing_period": "MONTHLY",
            "features": {"default_tokens": 10, "max_projects": 1},
            "trial_days": 0,
            "sort_order": 0,
        },
        {
            "name": "Starter",
            "slug": "starter",
            "description": "For individuals",
            "price_float": 9.99,
            "billing_period": "MONTHLY",
            "features": {"default_tokens": 100, "max_projects": 5},
            "trial_days": 14,
            "sort_order": 1,
        },
        {
            "name": "Professional",
            "slug": "professional",
            "description": "For teams",
            "price_float": 29.99,
            "billing_period": "MONTHLY",
            "features": {"default_tokens": 500, "max_projects": 20},
            "trial_days": 14,
            "sort_order": 2,
        },
        {
            "name": "Enterprise",
            "slug": "enterprise",
            "description": "For large organizations",
            "price_float": 99.99,
            "billing_period": "MONTHLY",
            "features": {"default_tokens": 2000, "max_projects": -1},
            "trial_days": 30,
            "sort_order": 3,
        },
    ]

    from uuid import uuid4
    from vbwd.models.enums import BillingPeriod

    created_plans = 0
    for plan_data in plans_data:
        existing = db.session.query(TarifPlan).filter_by(slug=plan_data["slug"]).first()
        if not existing:
            plan = TarifPlan(
                id=uuid4(),
                name=plan_data["name"],
                slug=plan_data["slug"],
                description=plan_data["description"],
                price_float=plan_data["price_float"],
                price=plan_data["price_float"],
                currency="EUR",
                billing_period=BillingPeriod(plan_data["billing_period"]),
                features=plan_data["features"],
                trial_days=plan_data["trial_days"],
                sort_order=plan_data["sort_order"],
                is_active=True,
            )
            db.session.add(plan)
            db.session.flush()
            category.tarif_plans.append(plan)
            created_plans += 1

    if created_plans:
        logger.info("[subscription] Created %d demo plans", created_plans)

    db.session.commit()
    logger.info("[subscription] populate_db complete")

    # Seed the checkout-confirmation page so /checkout/confirmation resolves
    # on every instance (subscription is enabled on all verticals). Idempotent
    # — safe even when shop/booking populate it too.
    try:
        from plugins.checkout.populate_db import populate_checkout_cms

        populate_checkout_cms()
    except ImportError:
        logger.info("[subscription] checkout plugin not installed — skipping checkout-confirmation page")

    # Email templates
    _populate_email_templates()


def _populate_email_templates():
    """Import subscription email templates."""
    import json
    import os

    templates_path = os.path.join(
        os.path.dirname(__file__),
        "docs", "imports", "email", "subscription-email-templates.json",
    )
    if not os.path.exists(templates_path):
        return

    try:
        from plugins.email.src.models.email_template import EmailTemplate
    except ImportError:
        return

    from uuid import uuid4

    with open(templates_path) as fh:
        templates = json.load(fh)

    for tpl in templates:
        existing = db.session.query(EmailTemplate).filter_by(event_type=tpl["event_type"]).first()
        if not existing:
            db.session.add(EmailTemplate(
                id=uuid4(),
                event_type=tpl["event_type"],
                subject=tpl["subject"],
                html_body=tpl["html_body"],
                text_body=tpl["text_body"],
                is_active=tpl.get("is_active", True),
            ))
            logger.info("[subscription] Created email template: %s", tpl["event_type"])

    db.session.commit()
