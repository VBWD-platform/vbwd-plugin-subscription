"""Subscription plugin CLI commands (S103.3).

Registered on the Flask app from the plugin's ``on_enable`` via
``current_app.cli.add_command`` — core stays agnostic (it declares no
subscription command). Mirrors the cms plugin's CLI registration.

    flask subscription run-billing                       # one cron pass
    flask subscription run-billing --as-of 2026-06-26    # treat that day as "now"
    flask subscription run-billing --dry-run             # preview, mutate nothing

``run-billing`` is the cron entrypoint for the same expire→convert-trials→
dunning pass the in-process scheduler runs (both call the one
``run_subscription_billing`` home — DRY).
"""
from datetime import datetime
import json

import click
from flask.cli import with_appcontext


@click.group("subscription")
def subscription_cli() -> None:
    """Subscription plugin maintenance commands."""


@subscription_cli.command("run-billing")
@click.option(
    "--as-of",
    "as_of",
    default=None,
    help="ISO date/datetime treated as 'now' for trial conversion "
    "(default: the real current time).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Report what would be processed; charge / cancel nothing.",
)
@with_appcontext
def run_billing_command(as_of, dry_run) -> None:
    """Run one subscription billing pass: expire ended subscriptions, convert
    ended trials (charge the checkout-selected method → ACTIVE, or CANCEL on
    failure), and send dunning. Prints a JSON summary."""
    from plugins.subscription.subscription.scheduler import run_subscription_billing

    now = _parse_as_of(as_of) if as_of else None
    summary = run_subscription_billing(now=now, dry_run=dry_run)
    click.echo(json.dumps(summary, default=str))


def _parse_as_of(value: str) -> datetime:
    """Parse an ISO date (``2026-06-26``) or datetime; raise a click error."""
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise click.BadParameter(
            f"--as-of must be an ISO date/datetime, got: {value!r}"
        )
