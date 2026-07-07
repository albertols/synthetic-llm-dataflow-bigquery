"""Unit tests for `EnforceUniqueness` — the uniqueness gate transform.

Full-row duplicates and repeated identity-column values divert to the
DLQ (first occurrence lands) instead of failing the whole batch.
"""

from __future__ import annotations

import apache_beam as beam
from apache_beam.testing.test_pipeline import TestPipeline
from apache_beam.testing.util import assert_that, equal_to
from sdfb_beam.dofns.uniqueness import EnforceUniqueness


def test_enforce_uniqueness_diverts_row_and_identity_duplicates():
    # r1/r2 are exact-duplicate rows (same id + value).
    # r3/r4 share identity column "id" but differ in "value" — identity dup,
    # not a row dup.
    r1 = {"id": 1, "value": "a"}
    r2 = {"id": 1, "value": "a"}
    r3 = {"id": 2, "value": "b"}
    r4 = {"id": 2, "value": "c"}
    records = [r1, r2, r3, r4]

    with TestPipeline() as p:
        result = (
            p
            | "Create" >> beam.Create(records)
            | "EnforceUniqueness" >> EnforceUniqueness(identity_columns=["id"])
        )
        unique = result["unique"]
        duplicates = result["duplicates"]

        assert_that(unique | "CountUnique" >> beam.combiners.Count.Globally(), equal_to([2]), label="unique_count")
        assert_that(
            duplicates | "RuleIds" >> beam.Map(lambda d: d["rule_id"]),
            equal_to(["row.duplicate", "identity.unique"]),
            label="duplicate_rule_ids",
        )
        assert_that(
            duplicates
            | "EnvelopeShape"
            >> beam.Map(
                lambda d: {"error_type", "stage"} <= set(d.keys())
                and d["error_type"] == "uniqueness"
                and d["stage"] == "pre_write"
            ),
            equal_to([True, True]),
            label="duplicate_envelope_shape",
        )
