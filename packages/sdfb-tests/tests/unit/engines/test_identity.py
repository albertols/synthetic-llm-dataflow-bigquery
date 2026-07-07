"""Identity-column synthesis contract tests.

The 2026-07 E2E report found identity/PK columns copied verbatim from the
source table (copy_ratio=1.0 — a re-identification leak). These tests pin
`synthesize_identity_value` / `apply_identity_columns` so identity columns
are always per-row-unique and never sampled from reference data.
"""

from __future__ import annotations

import uuid

from sdfb_core.engines.identity import (
    apply_identity_columns,
    synthesize_identity_value,
)


def test_string_identity_is_valid_uuid_and_deterministic():
    v1 = synthesize_identity_value("STRING", "run-a", 0, 0, "customer_id")
    v2 = synthesize_identity_value("STRING", "run-a", 0, 0, "customer_id")
    assert v1 == v2
    uuid.UUID(v1)  # raises if not UUID-shaped


def test_rows_and_batches_and_columns_are_unique():
    vals = {
        synthesize_identity_value("STRING", "run-a", b, r, c)
        for b in range(3)
        for r in range(50)
        for c in ("id_a", "id_b")
    }
    assert len(vals) == 3 * 50 * 2


def test_integer_identity_is_int():
    v = synthesize_identity_value("INTEGER", "run-a", 1, 2, "seq_id")
    assert isinstance(v, int) and v >= 0


def test_apply_overwrites_only_identity_columns():
    record = {"customer_id": "LEAKED-REAL-VALUE", "amount": 42}
    out = apply_identity_columns(
        record,
        identity_columns=["customer_id"],
        column_types={"customer_id": "STRING", "amount": "INTEGER"},
        run_id="run-a",
        batch_id=0,
        row_index=0,
    )
    assert out["amount"] == 42
    assert out["customer_id"] != "LEAKED-REAL-VALUE"
    uuid.UUID(out["customer_id"])
