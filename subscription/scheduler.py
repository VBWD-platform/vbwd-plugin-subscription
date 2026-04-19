"""Subscription lifecycle scheduler — expiration, trials, dunning.

Extracted from vbwd/scheduler.py (Sprint 04b).
"""
import logging

logger = logging.getLogger(__name__)


def run_subscription_jobs(app):
    """Periodic task: expire subscriptions, trials, send dunning."""
    with app.app_context():
        from vbwd.extensions import db
        from plugins.subscription.subscription.repositories.subscription_repository import (
            SubscriptionRepository,
        )
        from vbwd.repositories.invoice_repository import InvoiceRepository
        from plugins.subscription.subscription.services.subscription_service import (
            SubscriptionService,
        )

        repository = SubscriptionRepository(db.session)
        invoice_repository = InvoiceRepository(db.session)
        service = SubscriptionService(repository)
        expired = service.expire_subscriptions()
        trials = service.expire_trials(invoice_repository)
        dunning = service.send_dunning_emails()
        logger.info(
            "[Scheduler] Expired %d subscriptions, %d trials, %d dunning",
            len(expired),
            len(trials),
            len(dunning),
        )


def start_subscription_scheduler(app, interval_seconds=60):
    """Start the subscription lifecycle scheduler."""
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_subscription_jobs,
        "interval",
        seconds=interval_seconds,
        args=[app],
        id="subscription_jobs",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "[subscription] Scheduler started (interval=%ds)", interval_seconds
    )
    return scheduler
