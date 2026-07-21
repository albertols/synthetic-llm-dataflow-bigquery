"""Deterministic stratified 'bottom-k reservoir' (WS3 §2).

Same run_id + same row content ⇒ same sample, every time. Buckets are
trimmed at 2x cap so accumulator memory stays O(num_strata x cap) — the
same commutative/associative-accumulator contract as MergeProfilesFn.
Selection order is (sort_key, row_digest): the digest tie-break keeps
entries totally ordered without ever comparing row dicts.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field

from sdfb_core.evaluation.profile import StratificationPlan, stratum_key
from sdfb_core.validation.uniqueness import row_digest

_OVERALL_CAP = 50_000
_MIN_STRATUM_CAP = 1_000

# One reservoir entry: (sort_key, row_digest, row).
_Entry = tuple[int, str, dict]


def per_stratum_cap(num_strata: int, overall_cap: int = _OVERALL_CAP) -> int:
    return max(_MIN_STRATUM_CAP, overall_cap // max(num_strata, 1))


def sort_key(run_id: str, stratum: str, row: dict) -> int:
    """Deterministic bottom-k priority — smallest keys win."""
    digest = row_digest(row)
    h = hashlib.blake2b(f"{run_id}:{stratum}:{digest}".encode(), digest_size=8)
    return int.from_bytes(h.digest(), "big")


@dataclass
class ReservoirAccumulator:
    by_stratum: dict[str, list[_Entry]] = field(default_factory=dict)


def _trim(bucket: list[_Entry], cap: int) -> None:
    bucket.sort(key=lambda e: (e[0], e[1]))
    del bucket[cap:]


def add_row(
    acc: ReservoirAccumulator,
    row: dict,
    *,
    plan: StratificationPlan,
    run_id: str,
    cap: int,
) -> ReservoirAccumulator:
    stratum = stratum_key(plan, row)
    bucket = acc.by_stratum.setdefault(stratum, [])
    bucket.append((sort_key(run_id, stratum, row), row_digest(row), row))
    if len(bucket) >= 2 * cap:
        _trim(bucket, cap)
    return acc


def merge_accumulators(
    accs: Iterable[ReservoirAccumulator], *, cap: int
) -> ReservoirAccumulator:
    merged = ReservoirAccumulator()
    for acc in accs:
        for stratum, bucket in acc.by_stratum.items():
            target = merged.by_stratum.setdefault(stratum, [])
            target.extend(bucket)
            if len(target) >= 2 * cap:
                _trim(target, cap)
    return merged


def extract_sample(
    acc: ReservoirAccumulator, *, cap: int, overall_cap: int = _OVERALL_CAP
) -> list[dict]:
    """Trim every stratum to cap, then re-sort the union and trim globally —
    this is what guarantees the overall cap holds even when
    num_strata x cap overshoots it."""
    entries: list[_Entry] = []
    for bucket in acc.by_stratum.values():
        _trim(bucket, cap)
        entries.extend(bucket)
    entries.sort(key=lambda e: (e[0], e[1]))
    return [row for _, _, row in entries[:overall_cap]]
