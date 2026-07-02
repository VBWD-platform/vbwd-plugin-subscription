"""Vendor-mode contract + decoupling oracles.

The money path is decoupled: subscription stamps the buyer invoice line with a
LOCAL key literal and the central ``marketplace`` plugin credits the selling
vendor from it — subscription never imports marketplace. These tests pin the
literal (so the value can never drift from the documented ``vendor_id``
convention) and prove subscription's source names no ``plugins.marketplace``
import.
"""
import os


SUBSCRIPTION_SOURCE_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "subscription")
)


def test_vendor_id_key_literal_is_vendor_id():
    from plugins.subscription.subscription.constants import VENDOR_ID_KEY

    # Pinned to the documented marketplace convention WITHOUT importing
    # marketplace — DRY without inverting the dependency arrow.
    assert VENDOR_ID_KEY == "vendor_id"


def _python_files(root):
    for current_dir, _dirs, files in os.walk(root):
        if "__pycache__" in current_dir:
            continue
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(current_dir, name)


def test_subscription_source_does_not_import_marketplace():
    offenders = []
    for path in _python_files(SUBSCRIPTION_SOURCE_ROOT):
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
        if "plugins.marketplace" in content or "from plugins import marketplace" in (
            content
        ):
            offenders.append(path)
    assert not offenders, (
        "Subscription must not depend on the marketplace plugin — keep the money "
        f"path decoupled (stamp a literal, never import): {offenders}"
    )
