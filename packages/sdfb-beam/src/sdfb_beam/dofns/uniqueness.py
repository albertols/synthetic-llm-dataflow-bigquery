"""Uniqueness enforcement — duplicates divert to the DLQ instead of landing.

Full-row duplicates and repeated identity values are the two block-replay /
memorization signatures the 2026-07 E2E report found unguarded. Keying by
digest + GroupByKey keeps memory flat regardless of run size; the first
occurrence lands, the rest become DLQ rows whose ``rule_id`` feeds
``build_run_summary`` → the BLOCKER gate.

Iteration order within a ``GroupByKey`` group is not guaranteed by Beam —
"first occurrence wins" therefore means an arbitrary (but single) survivor
per key, not necessarily the record that appeared earliest in the input.
That is acceptable here: the goal is "keep exactly one", not "keep the
lexicographically/temporally first one".
"""

from __future__ import annotations

import apache_beam as beam
from sdfb_core.validation.uniqueness import row_digest

RULE_ROW_DUPLICATE = "row.duplicate"
RULE_IDENTITY_UNIQUE = "identity.unique"


def _envelope(record: dict, rule_id: str) -> dict:
    return {
        "raw_request": record,
        "error_type": "uniqueness",
        "error_detail": f"{rule_id}: duplicate of an earlier record in this run",
        "rule_id": rule_id,
        "stage": "pre_write",
    }


class _FirstWins(beam.DoFn):
    def __init__(self, rule_id: str) -> None:
        self.rule_id = rule_id

    def process(self, kv):
        _key, records = kv
        it = iter(records)
        yield next(it)
        for dup in it:
            yield beam.pvalue.TaggedOutput("duplicates", _envelope(dup, self.rule_id))


class EnforceUniqueness(beam.PTransform):
    """Diverts full-row and identity-column duplicates to the DLQ.

    Returns a ``dict`` with ``"unique"`` (main, deduplicated records) and
    ``"duplicates"`` (DLQ-envelope dicts) PCollections.

    The ROW digest is computed with identity columns excluded
    (``{k: v for k, v in r.items() if k not in identity_set}``). Identity
    columns (see ``sdfb_core.engines.identity``) are synthesized fresh per
    row from ``(run_id, batch_id, row_index, column)``, so two rows that are
    an exact engine-batch-replay in every OTHER field would still carry
    distinct identity values — including them in the row digest would mask
    the replay from ``row.duplicate`` entirely. Excluding them keeps the two
    rules covering disjoint failure modes: ``row.duplicate`` catches
    non-identity-field replay, ``identity.unique`` catches identity-value
    collisions, keyed on the final (post-identity-synthesis) rows.
    """

    def __init__(self, identity_columns: list[str] | None = None) -> None:
        super().__init__()
        self.identity_columns = list(identity_columns or [])

    def expand(self, records):
        identity_set = set(self.identity_columns)

        def _row_key(r, ids=identity_set):
            return row_digest({k: v for k, v in r.items() if k not in ids})

        by_row = (
            records
            | "KeyByRowDigest" >> beam.Map(lambda r: (_row_key(r), r))
            | "GroupByRowDigest" >> beam.GroupByKey()
            | "FirstRowWins"
            >> beam.ParDo(_FirstWins(RULE_ROW_DUPLICATE)).with_outputs(
                "duplicates", main="unique"
            )
        )
        row_unique = by_row.unique
        dup_streams = [by_row.duplicates]
        if self.identity_columns:
            cols = self.identity_columns
            by_id = (
                row_unique
                | "KeyByIdentity"
                >> beam.Map(lambda r, c=cols: (tuple(str(r.get(x)) for x in c), r))
                | "GroupByIdentity" >> beam.GroupByKey()
                | "FirstIdentityWins"
                >> beam.ParDo(_FirstWins(RULE_IDENTITY_UNIQUE)).with_outputs(
                    "duplicates", main="unique"
                )
            )
            row_unique = by_id.unique
            dup_streams.append(by_id.duplicates)
        duplicates = dup_streams | "FlattenDuplicates" >> beam.Flatten()
        return {"unique": row_unique, "duplicates": duplicates}
