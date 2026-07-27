"""Uniqueness enforcement — duplicates divert to the DLQ instead of landing.

Full-row duplicates and repeated identity values are the two block-replay /
memorization signatures the 2026-07 E2E report found unguarded. Keying by
digest + ``CombinePerKey`` keeps memory flat regardless of run size; one
occurrence lands, the rest become DLQ rows whose ``rule_id`` feeds
``build_run_summary`` → the BLOCKER gate.

``CombinePerKey``, not ``GroupByKey`` (WS6 W4): a GroupByKey materializes
every value for a key reducer-side, so the whole dataset crosses the
shuffle — the 2026-07-26 1M run peaked at 12.77 MiB/s reading it back.
Combining runs MAP-side first, so each worker collapses its own duplicates
and the shuffle carries roughly the unique set.

Which record survives is arbitrary (Beam orders neither GroupByKey values
nor combiner inputs) — the goal is "keep exactly one", not "keep the
temporally first one". Duplicate DLQ envelopes carry the survivor's
payload: equal ``row_digest`` means identical non-identity fields, so the
only thing not preserved is the dropped rows' freshly-synthesized identity
values. Duplicate COUNTS are exact, which is what the gate folds.

This transform is still a barrier — every ``CombinePerKey`` is a shuffle.
See ``--uniqueness_mode=streaming`` for the non-barrier path.
"""

from __future__ import annotations

import apache_beam as beam
from sdfb_core.validation.uniqueness import row_digest

RULE_ROW_DUPLICATE = "row.duplicate"
RULE_IDENTITY_UNIQUE = "identity.unique"
RULE_PK_DUPLICATE = "pk.duplicate"


def _envelope(record: dict, rule_id: str) -> dict:
    return {
        "raw_request": record,
        "error_type": "uniqueness",
        "error_detail": f"{rule_id}: duplicate of an earlier record in this run",
        "rule_id": rule_id,
        "stage": "pre_write",
    }


class _FirstWinsCombineFn(beam.CombineFn):
    """Keep ONE survivor per key and count everything else.

    `GroupByKey` materializes every value for a key on the reducer side, so
    the whole dataset crosses the shuffle (2026-07-26 1M run:
    `GroupByRowDigest/Read` peaked at 12.77 MiB/s). `CombinePerKey` combines
    **map-side** first, so each worker collapses its own duplicates and the
    shuffle carries roughly the unique set.

    The accumulator is `(survivor, seen)`. `seen` is the EXACT number of
    records for the key — the BLOCKER gate folds `dlq_by_rule` counts
    (`validation/summary.py`), so the count is the part that must be
    preserved bit-for-bit.

    Which record survives is arbitrary, exactly as it was under
    `GroupByKey` (Beam does not order values within a group).
    """

    def create_accumulator(self) -> tuple[dict | None, int]:
        return (None, 0)

    def add_input(
        self, accumulator: tuple[dict | None, int], element: dict
    ) -> tuple[dict | None, int]:
        survivor, seen = accumulator
        return (element if survivor is None else survivor, seen + 1)

    def merge_accumulators(
        self, accumulators
    ) -> tuple[dict | None, int]:
        survivor: dict | None = None
        seen = 0
        for acc_survivor, acc_seen in accumulators:
            if survivor is None and acc_survivor is not None:
                survivor = acc_survivor
            seen += acc_seen
        return (survivor, seen)

    def extract_output(
        self, accumulator: tuple[dict | None, int]
    ) -> tuple[dict | None, int]:
        return accumulator


class _ExpandCombined(beam.DoFn):
    """Turn `(key, (survivor, seen))` back into one survivor + `seen - 1`
    DLQ envelopes.

    The envelopes carry the SURVIVOR's payload rather than each dropped
    record's. For `row.duplicate` that is the same content by construction —
    equal `row_digest` means the non-identity fields are identical — the
    only loss being the dropped rows' freshly-synthesized identity values,
    which are meaningless by definition. Counts, which is what the gate
    folds, are exact.
    """

    def __init__(self, rule_id: str) -> None:
        self.rule_id = rule_id

    def process(self, kv):
        _key, (survivor, seen) = kv
        if survivor is None:  # pragma: no cover - defensive
            return
        yield survivor
        envelope = _envelope(survivor, self.rule_id)
        for _ in range(seen - 1):
            yield beam.pvalue.TaggedOutput("duplicates", envelope)


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

    def __init__(
        self,
        identity_columns: list[str] | None = None,
        pk_columns: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.identity_columns = list(identity_columns or [])
        self.pk_columns = list(pk_columns or [])

    def expand(self, records):
        identity_set = set(self.identity_columns)

        def _row_key(r, ids=identity_set):
            return row_digest({k: v for k, v in r.items() if k not in ids})

        by_row = (
            records
            | "KeyByRowDigest" >> beam.Map(lambda r: (_row_key(r), r))
            | "CombineByRowDigest" >> beam.CombinePerKey(_FirstWinsCombineFn())
            | "FirstRowWins"
            >> beam.ParDo(_ExpandCombined(RULE_ROW_DUPLICATE)).with_outputs(
                "duplicates", main="unique"
            )
        )
        row_unique = by_row.unique
        dup_streams = [by_row.duplicates]
        if self.pk_columns:
            pk_cols = self.pk_columns
            by_pk = (
                row_unique
                | "KeyByPk"
                >> beam.Map(lambda r, c=pk_cols: (tuple(str(r.get(x)) for x in c), r))
                | "CombineByPk" >> beam.CombinePerKey(_FirstWinsCombineFn())
                | "FirstPkWins"
                >> beam.ParDo(_ExpandCombined(RULE_PK_DUPLICATE)).with_outputs(
                    "duplicates", main="unique"
                )
            )
            row_unique = by_pk.unique
            dup_streams.append(by_pk.duplicates)
        if self.identity_columns:
            cols = self.identity_columns
            by_id = (
                row_unique
                | "KeyByIdentity"
                >> beam.Map(lambda r, c=cols: (tuple(str(r.get(x)) for x in c), r))
                | "CombineByIdentity" >> beam.CombinePerKey(_FirstWinsCombineFn())
                | "FirstIdentityWins"
                >> beam.ParDo(_ExpandCombined(RULE_IDENTITY_UNIQUE)).with_outputs(
                    "duplicates", main="unique"
                )
            )
            row_unique = by_id.unique
            dup_streams.append(by_id.duplicates)
        duplicates = dup_streams | "FlattenDuplicates" >> beam.Flatten()
        return {"unique": row_unique, "duplicates": duplicates}
