"""S103.2c — resolve a method code → its RecurringChargeProvider plugin.

The resolver mirrors the withdraw payout-provider precedent: map the checkout
method *code* to its ``plugin_id`` via the core ``PaymentMethodRepository``, then
find the enabled plugin that opted into ``RecurringChargeProvider`` whose
``metadata.name`` matches. Deps are injected so the unit needs no Flask app.
"""
from types import SimpleNamespace
from uuid import uuid4

from vbwd.plugins.payment_provider import ChargeResult, RecurringChargeProvider

from plugins.subscription.subscription.services.recurring_charge_resolver import (
    resolve_recurring_charger,
)


class _FakeChargerPlugin(RecurringChargeProvider):
    def __init__(self, name):
        self.metadata = SimpleNamespace(name=name)

    def charge_saved_method(self, *, user_id, invoice) -> ChargeResult:
        return ChargeResult(success=True)


class _PlainPlugin:
    """A plugin that did NOT opt into RecurringChargeProvider."""

    def __init__(self, name):
        self.metadata = SimpleNamespace(name=name)


class _FakePluginManager:
    def __init__(self, plugins):
        self._plugins = plugins

    def get_enabled_plugins(self):
        return self._plugins


class _FakePaymentMethodRepo:
    def __init__(self, mapping):
        self._mapping = mapping

    def find_by_code(self, code):
        plugin_id = self._mapping.get(code)
        if plugin_id is None:
            return None
        return SimpleNamespace(code=code, plugin_id=plugin_id)


def test_resolves_matching_recurring_charge_provider():
    charger = _FakeChargerPlugin("token_payment")
    plugin_manager = _FakePluginManager([_PlainPlugin("stripe"), charger])
    payment_method_repo = _FakePaymentMethodRepo({"token_balance": "token_payment"})

    result = resolve_recurring_charger(
        "token_balance",
        plugin_manager=plugin_manager,
        payment_method_repo=payment_method_repo,
    )
    assert result is charger


def test_returns_none_when_method_code_unknown():
    plugin_manager = _FakePluginManager([_FakeChargerPlugin("token_payment")])
    payment_method_repo = _FakePaymentMethodRepo({})

    result = resolve_recurring_charger(
        "no_such_code",
        plugin_manager=plugin_manager,
        payment_method_repo=payment_method_repo,
    )
    assert result is None


def test_returns_none_when_plugin_lacks_recurring_capability():
    # Method maps to a plugin that exists but did not opt into the capability.
    plugin_manager = _FakePluginManager([_PlainPlugin("invoice_plugin")])
    payment_method_repo = _FakePaymentMethodRepo({"invoice": "invoice_plugin"})

    result = resolve_recurring_charger(
        "invoice",
        plugin_manager=plugin_manager,
        payment_method_repo=payment_method_repo,
    )
    assert result is None


def test_returns_none_when_method_code_missing():
    plugin_manager = _FakePluginManager([_FakeChargerPlugin("token_payment")])
    payment_method_repo = _FakePaymentMethodRepo({"token_balance": "token_payment"})

    assert (
        resolve_recurring_charger(
            None,
            plugin_manager=plugin_manager,
            payment_method_repo=payment_method_repo,
        )
        is None
    )
    # An id that maps but no plugin matches by name → None.
    payment_method_repo = _FakePaymentMethodRepo({"x": str(uuid4())})
    assert (
        resolve_recurring_charger(
            "x",
            plugin_manager=plugin_manager,
            payment_method_repo=payment_method_repo,
        )
        is None
    )
