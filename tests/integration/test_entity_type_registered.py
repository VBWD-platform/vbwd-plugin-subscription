"""S77 — the subscription plugin registers its taggable entity types.

Registering ``tarif_plan`` and ``addon`` in ``on_enable`` is what lets the core
value endpoints (``GET|PUT /api/v1/admin/<type>/<id>/{tags,custom-fields}``)
resolve the type and return 200 (each gated by its own manage permission)
instead of 404, so the plan / add-on edit pages' Tags / Custom-fields blocks
work.
"""
import pytest

from vbwd.services.entity_type_registry import get_entity_type, is_registered


@pytest.mark.parametrize(
    "entity_type,manage_permission",
    [
        ("tarif_plan", "subscription.plans.manage"),
        ("addon", "subscription.addons.manage"),
    ],
)
def test_subscription_entity_type_registered(app, entity_type, manage_permission):
    """The app fixture boots with subscription enabled — types are registered."""
    assert is_registered(entity_type)
    registration = get_entity_type(entity_type)
    assert registration is not None
    assert registration.manage_permission == manage_permission
