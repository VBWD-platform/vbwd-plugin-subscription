"""S103.2c — resolve a checkout payment-method code → its charge provider.

Trial-end conversion re-charges the method the user selected at checkout. The
selected method is stored as a *code* (e.g. ``"token_balance"``); this resolver
maps that code to the enabled plugin that opted into the core
``RecurringChargeProvider`` capability — mirroring the withdraw plugin's
payout-provider precedent (``isinstance`` discovery over enabled plugins).

The pure function takes its collaborators (the plugin manager + the core
``PaymentMethodRepository``) as arguments so it unit-tests without a Flask app.
``build_recurring_charge_resolver`` is the production builder that reads them
from ``current_app`` and returns a one-arg ``Callable[[str], Optional[...]]``.
"""
from typing import Any, Callable, Optional

from vbwd.plugins.payment_provider import RecurringChargeProvider


def resolve_recurring_charger(
    method_code: Optional[str],
    *,
    plugin_manager: Any,
    payment_method_repo: Any,
) -> Optional[RecurringChargeProvider]:
    """Return the enabled ``RecurringChargeProvider`` for ``method_code``.

    Returns ``None`` when the code is empty/unknown, has no ``plugin_id``, or no
    enabled plugin both opted into the capability and matches that ``plugin_id``
    by ``metadata.name`` (e.g. a manual "invoice" method).
    """
    if not method_code:
        return None

    payment_method = payment_method_repo.find_by_code(method_code)
    if payment_method is None or not payment_method.plugin_id:
        return None
    plugin_id = payment_method.plugin_id

    for plugin in plugin_manager.get_enabled_plugins():
        # ``metadata`` lives on BasePlugin (every concrete plugin); the ABC only
        # adds the capability. Read the name off the loosely-typed plugin, then
        # narrow with isinstance.
        if getattr(plugin.metadata, "name", None) == plugin_id and isinstance(
            plugin, RecurringChargeProvider
        ):
            return plugin
    return None


def build_recurring_charge_resolver() -> (
    Callable[[Optional[str]], Optional[RecurringChargeProvider]]
):
    """Production builder: bind the resolver to ``current_app`` collaborators.

    Reads the plugin manager off ``current_app`` and builds a core
    ``PaymentMethodRepository`` on ``db.session`` (the same direct-construction
    idiom token_payment uses — core registers no payment-method DI provider).
    """
    from flask import current_app

    from vbwd.extensions import db
    from vbwd.repositories.payment_method_repository import PaymentMethodRepository

    plugin_manager = getattr(current_app, "plugin_manager", None)
    payment_method_repo = PaymentMethodRepository(db.session)

    def _resolver(method_code: Optional[str]) -> Optional[RecurringChargeProvider]:
        if plugin_manager is None:
            return None
        return resolve_recurring_charger(
            method_code,
            plugin_manager=plugin_manager,
            payment_method_repo=payment_method_repo,
        )

    return _resolver
