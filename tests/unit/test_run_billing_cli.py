"""S103.3 — the `flask subscription run-billing` shared billing home + parser.

The scheduler and the CLI both call ONE function, ``run_subscription_billing``
(DRY), so cron drives the exact same expire→convert-trials→dunning path the
60s APScheduler runs. These specs pin the orchestration (collaborators injected
so no app/DB is needed) and the ``--as-of`` parser, without touching the gate's
heavier integration coverage.
"""
from datetime import datetime

import click
import pytest

from plugins.subscription.subscription.cli import _parse_as_of
from plugins.subscription.subscription.scheduler import run_subscription_billing


class _FakeRepo:
    def __init__(self, trials=0, expired=0, dunning=0):
        self._trials = [object()] * trials
        self._expired = [object()] * expired
        self._dunning = [object()] * dunning
        self.find_trials_now = "unset"

    def find_expired_trials(self, now=None):
        self.find_trials_now = now
        return list(self._trials)

    def find_expired(self):
        return list(self._expired)

    def find_dunning_candidates(self, days):
        return list(self._dunning)


class _FakeService:
    def __init__(self):
        self.expired_called = False
        self.dunning_called = False

    def expire_subscriptions(self):
        self.expired_called = True
        return [object()]

    def send_dunning_emails(self):
        self.dunning_called = True
        return [object(), object()]


class _FakeTrialConversion:
    def __init__(self):
        self.called = False
        self.now = "unset"

    def convert_expired_trials(self, now=None):
        self.called = True
        self.now = now
        return [{"outcome": "charged"}]


class TestParseAsOf:
    def test_parses_a_plain_date(self):
        assert _parse_as_of("2026-06-26") == datetime(2026, 6, 26, 0, 0)

    def test_parses_a_full_datetime(self):
        assert _parse_as_of("2026-06-26T08:30:00") == datetime(2026, 6, 26, 8, 30)

    def test_rejects_garbage_with_a_click_error(self):
        with pytest.raises(click.BadParameter):
            _parse_as_of("not-a-date")


class TestRunSubscriptionBilling:
    def test_dry_run_counts_without_mutating(self):
        repo = _FakeRepo(trials=3, expired=2, dunning=1)
        trial_conversion = _FakeTrialConversion()
        summary = run_subscription_billing(
            now=datetime(2026, 6, 26),
            dry_run=True,
            subscription_repo=repo,
            trial_conversion_service=trial_conversion,
        )
        assert summary["dry_run"] is True
        assert summary["trials_due"] == 3
        assert summary["expired_due"] == 2
        assert summary["dunning_due"] >= 1
        # dry-run charges/cancels nothing
        assert trial_conversion.called is False
        # the injected clock reached the trial query
        assert repo.find_trials_now == datetime(2026, 6, 26)

    def test_execute_runs_every_job_and_threads_the_clock(self):
        repo = _FakeRepo()
        service = _FakeService()
        trial_conversion = _FakeTrialConversion()
        when = datetime(2026, 6, 26, 9, 0)
        summary = run_subscription_billing(
            now=when,
            dry_run=False,
            subscription_repo=repo,
            subscription_service=service,
            trial_conversion_service=trial_conversion,
        )
        assert summary["dry_run"] is False
        assert service.expired_called is True
        assert service.dunning_called is True
        assert trial_conversion.called is True
        assert trial_conversion.now == when
        assert summary["converted"] == 1
        assert summary["expired"] == 1
        assert summary["dunning"] == 2
