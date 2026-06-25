"""S103.3 — the `flask subscription run-billing` CLI is registered + runnable.

Drives the REAL command through the app's CLI runner (the plugin registered its
``subscription`` group in ``on_enable`` via ``current_app.cli.add_command``,
mirroring cms). ``--dry-run`` is read-only, so this is a safe end-to-end proof
that the command exists, parses ``--as-of``, and emits a JSON summary.
"""
import json


def test_run_billing_dry_run_command_is_registered_and_runs(app):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["subscription", "run-billing", "--dry-run", "--as-of", "2026-06-26"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["dry_run"] is True
    assert "trials_due" in payload
    assert "expired_due" in payload
    assert "dunning_due" in payload
    assert payload["as_of"].startswith("2026-06-26")
