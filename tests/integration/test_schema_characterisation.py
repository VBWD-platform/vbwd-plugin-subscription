"""Sprint 03 Baseline (E1) — schema characterisation.

Freezes the exact DDL fingerprint of the 5 subscription tables + 2 m2m
tables as a golden snapshot. The model move (core → plugin, inverted shim)
must not change a single column/type/nullable/PK/FK: this test, unchanged,
must stay GREEN before and after the move (E2 — behaviour-preserving
relocation; the Liskov contract between old and new definition site).

Golden-file pattern: first run writes the snapshot and passes (baseline
capture); every later run asserts equality against it. To re-baseline
deliberately, delete the snapshot file and re-run (review the diff in VCS).
"""
import json
from pathlib import Path

from sqlalchemy import inspect as sa_inspect

SUBSCRIPTION_TABLES = [
    "vbwd_subscription",
    "vbwd_tarif_plan",
    "vbwd_addon",
    "vbwd_addon_subscription",
    "vbwd_tarif_plan_category",
    "vbwd_addon_tarif_plans",
    "vbwd_tarif_plan_category_plans",
]

SNAPSHOT = Path(__file__).parent / "_schema_fingerprint.json"


def _fingerprint(engine) -> dict:
    inspector = sa_inspect(engine)
    fingerprint: dict = {}
    for table in SUBSCRIPTION_TABLES:
        columns = {
            col["name"]: {
                "type": str(col["type"]),
                "nullable": bool(col["nullable"]),
            }
            for col in inspector.get_columns(table)
        }
        pk = sorted(inspector.get_pk_constraint(table).get("constrained_columns", []))
        foreign_keys = sorted(
            (
                tuple(fk["constrained_columns"]),
                fk["referred_table"],
                tuple(fk["referred_columns"]),
            )
            for fk in inspector.get_foreign_keys(table)
        )
        fingerprint[table] = {
            "columns": dict(sorted(columns.items())),
            "primary_key": pk,
            "foreign_keys": [list(fk) for fk in foreign_keys],
        }
    return fingerprint


def test_subscription_schema_fingerprint_is_stable(db):
    current = _fingerprint(db.engine)

    if not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        # Baseline capture run — every table must at least exist.
        assert set(current) == set(SUBSCRIPTION_TABLES)
        return

    # Normalise `current` through JSON too: the inspector yields tuples while
    # the deserialised snapshot yields lists — compare like-for-like.
    current = json.loads(json.dumps(current, sort_keys=True))
    expected = json.loads(SNAPSHOT.read_text())
    assert current == expected, (
        "Subscription schema changed vs the Sprint 03 baseline snapshot. "
        "A model-relocation sprint must not alter schema (E2). If this change "
        "is intentional, it belongs in a separate RED-tested sprint."
    )
