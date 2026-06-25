"""Subscription lifecycle scheduler — expiration, trials, dunning.

Extracted from vbwd/scheduler.py (Sprint 04b).
"""
import logging

logger = logging.getLogger(__name__)


def run_subscription_billing(
    *,
    now=None,
    dry_run=False,
    subscription_repo=None,
    subscription_service=None,
    trial_conversion_service=None,
):
    """Run the subscription billing pass once (assumes an app context).

    The SINGLE home shared by the 60s APScheduler (``run_subscription_jobs``)
    and the ``flask subscription run-billing`` CLI (S103.3) — DRY, so cron and
    the in-process scheduler drive the exact same path: expire ACTIVE subs whose
    period ended, CONVERT ended trials (charge the checkout-selected method →
    ACTIVE, or CANCELLED on failure — S103.2), then send dunning.

    Args:
        now: Optional injected "now" (the CLI's ``--as-of``); threaded into the
            trial query/conversion. ``None`` ⇒ real ``utcnow()``.
        dry_run: When True, COUNT what would be processed (read-only) and charge
            / cancel nothing.
        subscription_repo / subscription_service / trial_conversion_service:
            Injectable collaborators (default ⇒ built from ``current_app`` / db),
            so the orchestration is unit-testable without an app or DB.

    Returns:
        A summary dict (counts; the per-trial ``outcomes`` on a real run).
    """
    from vbwd.utils.datetime_utils import utcnow
    from plugins.subscription.subscription.services.subscription_service import (
        SubscriptionService,
    )

    clock = now or utcnow()
    repository = subscription_repo or _default_subscription_repo()

    if dry_run:
        return {
            "dry_run": True,
            "as_of": str(clock),
            "trials_due": len(repository.find_expired_trials(now=clock)),
            "expired_due": len(repository.find_expired()),
            "dunning_due": sum(
                len(repository.find_dunning_candidates(days))
                for days in SubscriptionService.DUNNING_DAYS
            ),
        }

    service = subscription_service or SubscriptionService(repository)
    trial_conversion = trial_conversion_service or _default_trial_conversion_service()
    expired = service.expire_subscriptions()
    # S103.2: trial-end CHARGES the checkout-selected method and converts the
    # trial to ACTIVE (or CANCELLED on charge failure) — never an unconditional
    # cancel.
    converted = trial_conversion.convert_expired_trials(now=clock)
    dunning = service.send_dunning_emails()
    return {
        "dry_run": False,
        "as_of": str(clock),
        "expired": len(expired),
        "converted": len(converted),
        "dunning": len(dunning),
        "outcomes": converted,
    }


def _default_subscription_repo():
    from vbwd.extensions import db
    from plugins.subscription.subscription.repositories.subscription_repository import (
        SubscriptionRepository,
    )

    return SubscriptionRepository(db.session)


def _default_trial_conversion_service():
    from plugins.subscription.subscription.services.trial_conversion_service import (
        build_trial_conversion_service,
    )

    return build_trial_conversion_service()


def run_subscription_jobs(app):
    """Periodic task: expire subscriptions, convert trials, send dunning."""
    with app.app_context():
        summary = run_subscription_billing()
        logger.info(
            "[Scheduler] Expired %d subscriptions, converted %d trials, %d dunning",
            summary["expired"],
            summary["converted"],
            summary["dunning"],
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
    logger.info("[subscription] Scheduler started (interval=%ds)", interval_seconds)
    return scheduler
