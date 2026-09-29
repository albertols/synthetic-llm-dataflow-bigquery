#  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""Row membership: which synthetic rows reproduce a source record, whole
or all but one field, and whether the rows the generator read come back
more often than rows it never saw.

    driver: the panel (D3) ─ BatchEncoder ─► PanelRefs per table
      │   R, H non-key / row / key hashes in rank order (E = the first
      │   e_n ranks of R, H_E of H) and their cell-code matrices
      ▼
    EncodedBatch ─ Partition(table) ─┬─ source ─► SourceHashes ─► Combine
      │                              │   Globally: SourceSets (sorted uint64
      │                              │   non-key and row hashes) ── AsSingleton
      │                              └─ synthetic ─► MembershipFn ◄─ panel
      │                                   │  (Shared PanelIndex)   AsSingleton
      │                                   ├─ (table, MembershipAcc)
      │                                   │    ─► CombinePerKey ────────────┐
      │                                   └─ ((table, check), candidate)    │
      │                                        ─► Top.PerKey(k) ─► RowFlag   │
      │                                           (label key: AsSingleton)  │
      └─ RowKeysFn ─► ((table, kind, bucket), code counts)                  │
           ─► CombinePerKey ─► KeyStats ─► CombinePerKey(table) ────────────┤
    one seed per table ─────────────────────────────────────────────────────┤
                                               GroupByKey(table) ◄──────────┘
                                                 ─► MetricValue (OWNED_METRIC_IDS)

Hashes (`beam.encode`): `nonkey_hash` is the salted linear hash of the
non-key cells (no PK, identity or FK column), so a record copied under a
fresh key still matches; `row_hash` covers every column. A row whose
every non-key cell is NULL has one constant non-key hash: it carries no
record content and is NEVER a match, near match or internal duplicate
(Ruling R60); a row whose every cell is NULL never matches either.

Exact sets and the lifts (D3). With U_S the distinct non-key hashes of
panel set S, the exclusive sets are R \\ H and H \\ R — a record both
halves hold is common, and hitting it is chance — and, for the exposure
lift, E \\ H against H_E \\ R; R\\E and H\\H_E (the exclusive records
outside the exposed prefixes) give `detail.unexposed`. R and H are
exchangeable halves of one ranking, so under the null a synthetic
generator hits either exclusive set alike. The EVENT is a distinct
exclusive record the synthetic side reproduces at least once, never a
synthetic row: a record hit by many synthetic rows is one event, which
keeps the counts close to Poisson. Counting rows instead would weight
every chance hit by the synthetic rows its pattern draws, which on a
skewed table puts the whole R/H selection noise into a narrow Poisson
interval (a simulation at n_syn ≫ n missed 1 in 39-81 % of null runs,
against 0-1 % for distinct records). The row counts are in `detail`
(`rows_r`/`rows_h`); a record copied many times still shows in
`row.exact_match_rate_nonkey` and in the flags. The lift is the
conditional rate-ratio test of the event counts over the exclusive set
sizes, with a Clopper-Pearson interval (Przyborowski & Wilenski, 1940;
Clopper & Pearson, 1934); status reads ci_low (Rulings R1, R12, R38: no
event on either side leaves the value NULL with ci_low 0).

Near matches (leave one out, Ruling R2). Row x's key at non-key column j
is `canonical.loo_hashes`: its non-key hash without column j's term, so
two rows equal in every non-key column but j share it. A synthetic row
near-matches set S when, at some j, its key equals the key of an S row at
the same j AND every other non-key cell code equals that row's
(`hash64` per cell, compared exactly: a linear-hash collision is
rejected), and it does not match an S record exactly. The dropped
column's all-NULL remainder is never a key (R60). `row.near_match_rate`
is the share of synthetic rows near-matching R (Wilson, 1927); the near
lift counts distinct exclusive keys (a key of R not held by H at the same
column, and vice versa), as the exact lift does. Fewer than two non-key
columns leave nothing to compare once one is dropped.

The full source. `row.exact_match_rate` (every column) and
`row.exact_match_rate_nonkey` compare the synthetic side with every
source row read:

    source side input ≤ SOURCE_SET_MAX_BYTES     sorted distinct source
      (8 B per row and array; an array for        hashes, one CombineGlobally
      the non-key hash, one for the row hash      per table, read with
      when the table has keys: 20M rows           AsSingleton; membership is
      key-less, 10M keyed at 160 MB)              map-side, so full-source
                                                  exact copies are flagged
    above it, or the source count unknown        the keyed count below IS the
                                                  co-grouping: Σ c_syn over
                                                  the hashes with c_src ≥ 1;
                                                  no full-source flag (panel
                                                  flags remain)

Task 26 sets `max_cache_memory_usage_mb` (Beam 2.74 defaults to 0, no
side-input cache) above the largest side input so a worker materialises
it once; the derived panel index is built once per worker behind
`apache_beam.utils.shared.Shared`, tagged by the panel's content token.

Keyed counts (all tables, one pass). Every hash is counted exactly per
side, in `(table, kind, bucket)` keys whose value holds the bucket's
sorted codes with their source and synthetic counts (numpy arrays) — the
hash space split by its top bits into up to 2^MAX_BUCKET_BITS buckets of
about KEY_BUCKET_CODES codes each, so a batch sends one element per
bucket instead of one per row:

    nk     non-key hash, both sides   internal duplicates (rows in a hash
                                      counted ≥ 2, over rows with content);
                                      the keyed full-source non-key matches
    pk     PK hash, synthetic         table.pk_duplicate_rate (a NULL part is
    id     identity hash, synthetic   no key: counted as `null_key_rows`,
                                      never a duplicate)
    row    row hash, both sides       keyed mode only, keyed tables only

Internal duplicates are counted on the FULL data of each side (Ruling
R59), never the matched-n subsample: `row.internal_duplicate_excess` =
d_syn - d_src with a Newcombe interval (Newcombe, 1998; informational:
rows of a duplicate group are not independent, and the catalogue gives
this metric no noise floor), `baseline_value` = d_R - d_src (D4).

Flags. `Top.PerKey(row_flags_top_k)` per (table, check) for `exact_copy`
and `near_copy`, ranked by the set a row copies (E, then R, H, the full
source), then a salted SplitMix64 spread of its row hash (Steele, Lea &
Flood, 2014), then its synthetic key: deterministic for an evaluation,
and a sample, not the first rows read. A flag carries the synthetic PK
(the synthetic table's own key, as JSON) and the matched source record's
key as a KEYED label (`canonical.hashed_label` of its PK hash — identity
hash, row hash for a key-less table — with the worker-side label key,
Rulings R64, R68); `source_key` stays NULL. No attribute value is ever
written. A full-source copy has no panel row to name, so its
`source_key_hash` is NULL. With an unverified reference only full-source
flags are written: the panel sets are not known to be the generator's.

An unverified reference (`Panel.verified` False) makes every R/H/E-based
metric `not_evaluated` with `UNVERIFIED_REASON` (Review Focus 5); the
full-source metrics are still computed.

References (author-year, R22): Przyborowski & Wilenski (1940); Clopper &
Pearson (1934); Wilson (1927); Newcombe (1998); Steele, Lea & Flood
(2014); Stadler, Oprisanu & Troncoso (2022) for near copies.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, NamedTuple

import apache_beam as beam
import numpy as np
from apache_beam.utils.shared import Shared

from sdfb_evaluation.beam.encode import BatchEncoder, BatchLayout, EncodedBatch
from sdfb_evaluation.canonical import (
    NULL_CODE,
    canonical_value,
    hash64,
    hashed_label,
    json_safe,
    linear_hash,
    loo_hashes,
    multipliers,
)
from sdfb_evaluation.stats import noise
from sdfb_evaluation.types import Method, MetricValue, Side

if TYPE_CHECKING:
  from sdfb_evaluation.context.plan import TablePlan

__all__ = [
    "EXACT_COPY",
    "KEYED_COUNT",
    "KEY_BUCKET_CODES",
    "LIFT_ALPHA",
    "MAX_BUCKET_BITS",
    "NEAR_COPY",
    "OWNED_METRIC_IDS",
    "PANEL_SET_MAX_BYTES",
    "SIDE_INPUT",
    "SOURCE_SETS",
    "SOURCE_SET_MAX_BYTES",
    "UNVERIFIED_REASON",
    "KeyCountsCombineFn",
    "KeyStats",
    "Membership",
    "MembershipAcc",
    "MembershipCombineFn",
    "MembershipFn",
    "MembershipResult",
    "MembershipSpec",
    "PanelIndex",
    "PanelRefs",
    "PanelStats",
    "RowFlag",
    "RowKeysFn",
    "SourceSets",
    "SourceSetsCombineFn",
    "batch_membership",
    "flag_row",
    "key_counts",
    "membership_outputs",
    "source_hashes",
    "table_outputs",
]

OWNED_METRIC_IDS: tuple[str, ...] = (
    "row.exact_match_rate",
    "row.exact_match_rate_nonkey",
    "row.memorization_lift",
    "row.exposure_lift",
    "row.near_match_rate",
    "row.near_match_lift",
    "row.internal_duplicate_excess",
    "table.pk_duplicate_rate",
    "table.identity_duplicate_rate",
)

PANEL_SET_MAX_BYTES = 16 * 2**20  # one panel side-input array
SOURCE_SET_MAX_BYTES = 160_000_000  # the full-source side input, all arrays
EXACT_COPY, NEAR_COPY = "exact_copy", "near_copy"
SOURCE_SETS: tuple[str, ...] = ("E", "R", "H", "source")  # flag priority
SIDE_INPUT, KEYED_COUNT = "side_input", "keyed_count"
LIFT_ALPHA = 0.05
UNVERIFIED_REASON = "reference sample not verified (digest mismatch)"
KEY_BUCKET_CODES = 1 << 20  # codes per keyed-count bucket, about
MAX_BUCKET_BITS = 12
_UNKNOWN_BUCKET_BITS = 4
_COMPACT_CODES = 1 << 20  # pending codes before a combine compacts
_DUPLICATED = 2  # a hash counted this often or more is a duplicate
_MIN_NEAR_COLUMNS = 2  # dropping one of fewer leaves nothing to compare
# flag priority = the index of the copied set in SOURCE_SETS
_PRIO_E, _PRIO_R, _PRIO_H, _PRIO_SOURCE = range(4)
# a panel record's class (`_classes`)
_SHARED, _EXPOSED, _REST = 0, 1, 2
_MB = 1e6

_NO_SYNTHETIC = "no synthetic rows were read"
_NO_SOURCE = "no source rows were read"
_NO_CONTENT = ("every column is a key (primary key, identity or foreign "
               "key): there is no non-key content to match")
_NO_PANEL = "no reference panel (R/H) was read for this table"
_FEW_COLUMNS = ("fewer than 2 non-key columns: dropping one leaves nothing "
                "to compare")
_NO_PK = "no primary key declared in the relationship model"
_NO_IDENTITY = "no identity columns declared in the relationship model"
_NO_EXPOSURE = ("no non-key record is held only by {a}, or none only by {b} "
                "(an exclusive set is empty): the lift has no exposure on one "
                "side")
_NULL_KEY_NOTE = ("rows whose key has a NULL part are not duplicates; a NULL "
                  "key is its own integrity defect")

_INT_TYPES = frozenset({"INT64", "INTEGER"})
_COUNTED_SIDES = frozenset({Side.SOURCE, Side.SYNTHETIC})
_SPLITMIX = (np.uint64(0x9E3779B97F4A7C15), np.uint64(0xBF58476D1CE4E5B9),
             np.uint64(0x94D049BB133111EB))
_SPREAD_LABEL = "sdfb:flag-spread"
_ACCS, _FLAGS, _STATS, _COUNTS = "accs", "flags", "stats", "counts"
_ACC, _KEYS, _SEED = "acc", "keys", "seed"
_EMPTY_POS = np.zeros(0, dtype=np.int64)
_EMPTY_CODES = np.zeros(0, dtype=np.uint64)


def _mix64(x: np.ndarray) -> np.ndarray:
  """SplitMix64's finaliser, vectorised (uint64 wraps by design)."""
  with np.errstate(over="ignore"):
    z = x + _SPLITMIX[0]
    z = (z ^ (z >> np.uint64(30))) * _SPLITMIX[1]
    z = (z ^ (z >> np.uint64(27))) * _SPLITMIX[2]
    return z ^ (z >> np.uint64(31))


def _member(sorted_codes: np.ndarray, codes: np.ndarray) -> np.ndarray:
  """`codes[i] in sorted_codes`, vectorised (`sorted_codes` sorted)."""
  if sorted_codes.size == 0 or codes.size == 0:
    return np.zeros(len(codes), dtype=bool)
  pos = np.minimum(np.searchsorted(sorted_codes, codes), len(sorted_codes) - 1)
  found: np.ndarray = sorted_codes[pos] == codes
  return found


def _lookup(sorted_codes: np.ndarray, codes: np.ndarray,
            eligible: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
  """(position of each code in `sorted_codes`, clipped; found & eligible)."""
  if sorted_codes.size == 0:
    return (np.zeros(len(codes),
                     dtype=np.int64), np.zeros(len(codes), dtype=bool))
  pos = np.minimum(np.searchsorted(sorted_codes, codes), len(sorted_codes) - 1)
  return pos, eligible & (sorted_codes[pos] == codes)


def _first_unique(codes: np.ndarray,
                  keep: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
  """(sorted distinct `codes[keep]`, the rank of each one's first row)."""
  ranks = np.nonzero(keep)[0]
  unique, first = np.unique(codes[ranks], return_index=True)
  return unique.astype(np.uint64), ranks[first].astype(np.int64)


def _positions(pos: np.ndarray, hit: np.ndarray) -> np.ndarray:
  """The distinct panel positions `pos[hit]`, as int64."""
  unique: np.ndarray = np.unique(pos[hit]).astype(np.int64)
  return unique


def _mb(nbytes: float) -> str:
  return f"{nbytes / _MB:.3g} MB"


def _union(a: np.ndarray, b: np.ndarray) -> np.ndarray:
  if a.size == 0:
    return b
  if b.size == 0:
    return a
  merged: np.ndarray = np.union1d(a, b)
  return merged


# --------------------------------------------------------------------------
# the per-table spec (driver-built, pickled into the DoFns: slim)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class MembershipSpec:  # pylint: disable=too-many-instance-attributes  # one field per fact the passes read
  """What membership needs of one `TablePlan` — never its panel rows
  (`PanelRefs` carries their hashes, as a side input).

  `null_content`/`null_row` are the non-key / row hashes of a row whose
  cells are all NULL (R60); `source_mode` is SIDE_INPUT or KEYED_COUNT
  (module docstring), `source_note` says why; `bucket_bits` splits the
  keyed counts.
  """
  table: str
  encoding_plan_digest: str
  salt: str
  nonkey_columns: tuple[str, ...]
  keyed: bool
  pk: tuple[str, ...]
  identity: tuple[str, ...]
  pk_text: tuple[int, ...] | None
  pk_int: tuple[bool, ...]
  null_content: int
  null_row: int
  source_mode: str
  source_note: str
  bucket_bits: int
  panel_max_bytes: int

  @classmethod
  def from_table(
      cls,
      table: TablePlan,
      *,
      salt: str,
      panel_max_bytes: int = PANEL_SET_MAX_BYTES,
      source_set_max_bytes: int = SOURCE_SET_MAX_BYTES) -> MembershipSpec:
    layout = BatchLayout.from_table(table)
    nonkey = layout.nonkey_columns
    keyed = nonkey != layout.columns
    null_row = int(
        linear_hash(
            np.full((1, len(layout.columns)), NULL_CODE, dtype=np.uint64),
            multipliers(layout.columns, salt))[0])
    null_content = int(
        linear_hash(
            np.full((1, len(nonkey)), NULL_CODE, dtype=np.uint64),
            multipliers(nonkey, salt))[0]) if nonkey else NULL_CODE
    bq_type = {c.name: c.bq_type for c in table.columns}
    pk_text = (
        tuple(layout.text_columns.index(c) for c in layout.pk)
        if layout.pk and set(layout.pk) <= set(layout.text_columns) else None)
    mode, note = _source_mode(
        table, nonkey=bool(nonkey), keyed=keyed, max_bytes=source_set_max_bytes)
    return cls(
        table=table.name,
        encoding_plan_digest=table.encoding_plan_digest,
        salt=salt,
        nonkey_columns=nonkey,
        keyed=keyed,
        pk=layout.pk,
        identity=layout.identity,
        pk_text=pk_text,
        pk_int=tuple(bq_type[c].upper() in _INT_TYPES for c in layout.pk),
        null_content=null_content,
        null_row=null_row,
        source_mode=mode,
        source_note=note,
        bucket_bits=_bucket_bits(table),
        panel_max_bytes=panel_max_bytes)


def _source_mode(table: TablePlan, *, nonkey: bool, keyed: bool,
                 max_bytes: int) -> tuple[str, str]:
  """SIDE_INPUT when the sorted source sets fit `max_bytes` (8 B per
  source row and array: the non-key hashes when there is content, the row
  hashes when the table has keys), else KEYED_COUNT."""
  arrays = int(nonkey) + int(keyed)
  if table.rows_source is None:
    return KEYED_COUNT, "source row count unknown: the keyed count"
  need = table.rows_read[0] * 8 * arrays
  if need <= max_bytes:
    return SIDE_INPUT, f"source sets {_mb(need)} <= {_mb(max_bytes)}"
  return KEYED_COUNT, f"source sets {_mb(need)} > {_mb(max_bytes)}"


def _bucket_bits(table: TablePlan) -> int:
  """log2 of the keyed-count buckets: about KEY_BUCKET_CODES codes each."""
  if table.rows_source is None or table.rows_synthetic is None:
    return _UNKNOWN_BUCKET_BITS
  rows = sum(table.rows_read)
  if rows <= KEY_BUCKET_CODES:
    return 0
  return min(MAX_BUCKET_BITS, math.ceil(math.log2(rows / KEY_BUCKET_CODES)))


# --------------------------------------------------------------------------
# the panel: hashes (driver) and the derived index (worker, Shared)
# --------------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class PanelRefs:  # pylint: disable=too-many-instance-attributes  # one field per panel array
  """The R/H panel of one table as hashes, in rank order (the membership
  side input). E is `r_*[:e_n]`, H_E `h_*[:he_n]`. `r_key`/`h_key` are the
  rows' source-key codes (PK hash; identity hash; the row hash of a
  key-less table), labelled only at flag time with the worker's key.
  `r_cells`/`h_cells` are the (n, d) non-key cell codes (near matches).
  `lift_reason`/`near_reason` say why the exact / near sets are absent
  (their arrays are then empty); `r_dup` = (rows of R in a non-key hash
  held twice or more, R rows with content) for the D4 baseline."""
  table: str
  token: str
  r_nonkey: np.ndarray
  h_nonkey: np.ndarray
  r_row: np.ndarray
  h_row: np.ndarray
  r_key: np.ndarray
  h_key: np.ndarray
  r_cells: np.ndarray | None
  h_cells: np.ndarray | None
  e_n: int
  he_n: int
  lift_reason: str | None
  near_reason: str | None
  r_dup: tuple[int, int] | None

  @classmethod
  def from_table(cls, table: TablePlan, spec: MembershipSpec) -> PanelRefs:
    """The panel's hashes (the plan's salt, `BatchEncoder`); every R/H
    reason decided here, in order: no content, no panel, unverified, then
    the size guard (`spec.panel_max_bytes` per array)."""
    panel = table.panel
    empty = cls(table.name, "", _EMPTY_CODES, _EMPTY_CODES, _EMPTY_CODES,
                _EMPTY_CODES, _EMPTY_CODES, _EMPTY_CODES, None, None, 0, 0,
                _NO_PANEL, _NO_PANEL, None)
    if not spec.nonkey_columns:
      return replace(empty, lift_reason=_NO_CONTENT, near_reason=_NO_CONTENT)
    if panel is None:
      return empty
    r = _encode_panel(table, Side.REFERENCE, panel.r_rows, spec.salt)
    h = _encode_panel(table, Side.HOLDOUT, panel.h_rows, spec.salt)
    r_dup = _dup_rows(r[0], spec.null_content)
    lift_reason = None if panel.verified else UNVERIFIED_REASON
    vector_bytes = max(len(r[0]), len(h[0])) * 8
    if lift_reason is None and vector_bytes > spec.panel_max_bytes:
      lift_reason = (f"the reference hash side input would be "
                     f"{_mb(vector_bytes)}, above the "
                     f"{_mb(spec.panel_max_bytes)} cap per panel array")
    near_reason = lift_reason
    cell_bytes = max(r[3].nbytes, h[3].nbytes)
    if near_reason is None and len(spec.nonkey_columns) < _MIN_NEAR_COLUMNS:
      near_reason = _FEW_COLUMNS
    if near_reason is None and cell_bytes > spec.panel_max_bytes:
      near_reason = (f"the reference cell matrix would be {_mb(cell_bytes)}, "
                     f"above the {_mb(spec.panel_max_bytes)} cap per panel "
                     "array")
    if lift_reason is not None:
      return replace(
          empty, lift_reason=lift_reason, near_reason=near_reason, r_dup=r_dup)
    cells = (None, None) if near_reason is not None else (r[3], h[3])
    arrays = (*r[:3], *h[:3], *(c for c in cells if c is not None))
    return cls(
        table=table.name,
        token=_token(table.name, spec.salt, arrays),
        r_nonkey=r[0],
        r_row=r[1],
        r_key=r[2],
        h_nonkey=h[0],
        h_row=h[1],
        h_key=h[2],
        r_cells=cells[0],
        h_cells=cells[1],
        e_n=panel.e_n,
        he_n=panel.he_n,
        lift_reason=None,
        near_reason=near_reason,
        r_dup=r_dup)


def _encode_panel(
    table: TablePlan, side: Side, rows: Sequence[Mapping[str, Any]],
    salt: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
  """(non-key hashes, row hashes, key codes, cell codes) of panel rows."""
  if not rows:
    d = len(BatchLayout.from_table(table).nonkey_columns)
    return (_EMPTY_CODES, _EMPTY_CODES, _EMPTY_CODES,
            np.zeros((0, d), dtype=np.uint64))
  batch = BatchEncoder.from_table(
      table, side, salt=salt, subsample_rate=1.0).encode(rows)
  layout = batch.layout
  if layout.pk:
    key = batch.pk_hash
  elif layout.identity:
    key = batch.identity_hash
  else:
    key = batch.row_hash
  return batch.nonkey_hash, batch.row_hash, key, batch.h_nonkey


def _dup_rows(nonkey: np.ndarray, null_content: int) -> tuple[int, int]:
  """(rows in a non-key hash held twice or more, rows with content)."""
  content = nonkey[nonkey != np.uint64(null_content)]
  if content.size == 0:
    return 0, 0
  _, counts = np.unique(content, return_counts=True)
  return int(counts[counts >= _DUPLICATED].sum()), len(content)


def _token(table: str, salt: str, arrays: Iterable[np.ndarray]) -> str:
  """A digest of the panel's content: the `Shared` tag of its index."""
  digest = hashlib.blake2b(f"{table}\x1f{salt}".encode(), digest_size=16)
  for array in arrays:
    digest.update(np.ascontiguousarray(array).tobytes())
  return digest.hexdigest()


@dataclass(frozen=True, eq=False)
class _Loo:
  """One side's leave-one-out keys: per column j, the sorted distinct keys
  `keys[offsets[j]:offsets[j + 1]]`, the rank of each key's first row, the
  side's cell codes for the exact check, and `only` (the key is absent
  from the twin side's keys at the same column)."""
  keys: np.ndarray
  offsets: np.ndarray
  first: np.ndarray
  cells: np.ndarray
  only: np.ndarray

  @classmethod
  def build(cls, cells: np.ndarray, totals: np.ndarray, a: np.ndarray,
            null_loo: np.ndarray) -> _Loo:
    loo = loo_hashes(cells, a, totals) if cells.size else np.zeros(
        cells.shape, dtype=np.uint64)
    keys, firsts, offsets = [], [], [0]
    for j in range(cells.shape[1]):
      unique, first = _first_unique(loo[:, j], loo[:, j] != null_loo[j])
      keys.append(unique)
      firsts.append(first)
      offsets.append(offsets[-1] + len(unique))
    return cls(
        keys=np.concatenate(keys) if keys else _EMPTY_CODES,
        offsets=np.array(offsets, dtype=np.int64),
        first=np.concatenate(firsts) if firsts else _EMPTY_POS,
        cells=cells,
        only=np.ones(offsets[-1], dtype=bool))

  def column(self, j: int) -> np.ndarray:
    return self.keys[self.offsets[j]:self.offsets[j + 1]]

  def exclusive_of(self, twin: _Loo) -> _Loo:
    """This side with `only` set against `twin`, column by column."""
    only = np.concatenate([
        ~np.isin(self.column(j), twin.column(j))
        for j in range(len(self.offsets) - 1)
    ]) if len(self.offsets) > 1 else np.zeros(
        0, dtype=bool)
    return replace(self, only=only)


class PanelStats(NamedTuple):
  """The panel's exposures and reasons, for the emitter (slim ints)."""
  n_r: int
  n_h: int
  e_n: int
  he_n: int
  lift_reason: str | None
  near_reason: str | None
  t_e: int  # |E \ H|
  t_r_rest: int  # |(R \ H) \ E|
  t_he: int  # |H_E \ R|
  t_h_rest: int  # |(H \ R) \ H_E|
  t_near_r: int  # R's leave-one-out keys H lacks at the same column
  t_near_h: int
  r_dup: tuple[int, int] | None


@dataclass(eq=False)
class PanelIndex:  # pylint: disable=too-many-instance-attributes  # one field per lookup structure
  """The panel's lookup structures, built from `PanelRefs` once per worker
  (`Shared`) and on the driver for `stats` (the same function, so both
  agree). Position arrays index `u_r`/`u_h` (distinct non-key hashes with
  content); `r_class`/`h_class` classify each position: 0 not exclusive,
  1 exclusive and in the exposed prefix (E \\ H, H_E \\ R), 2 exclusive
  outside it."""
  exact: bool
  near: bool
  u_r: np.ndarray
  r_first: np.ndarray
  r_class: np.ndarray
  u_h: np.ndarray
  h_first: np.ndarray
  h_class: np.ndarray
  r_rows: np.ndarray
  h_rows: np.ndarray
  r_key: np.ndarray
  h_key: np.ndarray
  e_n: int
  a_nonkey: np.ndarray
  loo_r: _Loo | None
  loo_h: _Loo | None
  stats: PanelStats

  @classmethod
  def build(cls, spec: MembershipSpec, refs: PanelRefs) -> PanelIndex:
    content = np.uint64(spec.null_content)
    u_r, r_first = _first_unique(refs.r_nonkey, refs.r_nonkey != content)
    u_h, h_first = _first_unique(refs.h_nonkey, refs.h_nonkey != content)
    r_class = _classes(u_r, u_h, refs.r_nonkey[:refs.e_n])
    h_class = _classes(u_h, u_r, refs.h_nonkey[:refs.he_n])
    a = multipliers(spec.nonkey_columns, spec.salt)
    loo_r = loo_h = None
    if refs.r_cells is not None and refs.h_cells is not None:
      null_loo = loo_hashes(
          np.full((1, len(spec.nonkey_columns)), NULL_CODE, dtype=np.uint64), a,
          np.array([spec.null_content], dtype=np.uint64))[0]
      r_side = _Loo.build(refs.r_cells, refs.r_nonkey, a, null_loo)
      h_side = _Loo.build(refs.h_cells, refs.h_nonkey, a, null_loo)
      loo_r, loo_h = r_side.exclusive_of(h_side), h_side.exclusive_of(r_side)
    stats = PanelStats(
        n_r=len(refs.r_nonkey),
        n_h=len(refs.h_nonkey),
        e_n=refs.e_n,
        he_n=refs.he_n,
        lift_reason=refs.lift_reason,
        near_reason=refs.near_reason,
        t_e=int((r_class == _EXPOSED).sum()),
        t_r_rest=int((r_class == _REST).sum()),
        t_he=int((h_class == _EXPOSED).sum()),
        t_h_rest=int((h_class == _REST).sum()),
        t_near_r=int(loo_r.only.sum()) if loo_r is not None else 0,
        t_near_h=int(loo_h.only.sum()) if loo_h is not None else 0,
        r_dup=refs.r_dup)
    return cls(
        exact=refs.lift_reason is None,
        near=loo_r is not None,
        u_r=u_r,
        r_first=r_first,
        r_class=r_class,
        u_h=u_h,
        h_first=h_first,
        h_class=h_class,
        r_rows=np.unique(refs.r_row),
        h_rows=np.unique(refs.h_row),
        r_key=refs.r_key,
        h_key=refs.h_key,
        e_n=refs.e_n,
        a_nonkey=a,
        loo_r=loo_r,
        loo_h=loo_h,
        stats=stats)


def _classes(own: np.ndarray, twin: np.ndarray,
             exposed: np.ndarray) -> np.ndarray:
  """0 = also held by the twin set, 1 = exclusive and in the exposed
  prefix, 2 = exclusive outside it."""
  exclusive = ~np.isin(own, twin)
  in_prefix = np.isin(own, exposed)
  out: np.ndarray = np.where(exclusive, np.where(in_prefix, _EXPOSED, _REST),
                             0).astype(np.int8)
  return out


# --------------------------------------------------------------------------
# the full-source sets (side-input mode)
# --------------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class SourceSets:
  """The distinct source hashes of one table: non-key (content rows) and,
  for a table with keys, row hashes (all-NULL rows left out); sorted."""
  nonkey: np.ndarray
  row: np.ndarray | None


def source_hashes(spec: MembershipSpec, batch: EncodedBatch) -> SourceSets:
  """One source batch's distinct hashes (the combine's input)."""
  nonkey = _EMPTY_CODES
  if spec.nonkey_columns:
    nk = batch.nonkey_hash
    nonkey = np.unique(nk[nk != np.uint64(spec.null_content)])
  row = None
  if spec.keyed:
    rh = batch.row_hash
    row = np.unique(rh[rh != np.uint64(spec.null_row)])
  return SourceSets(nonkey, row)


def _codes_held(sets: SourceSets) -> int:
  return len(sets.nonkey) + (0 if sets.row is None else len(sets.row))


class SourceSetsCombineFn(beam.CombineFn):
  """Distinct sorted union of `SourceSets` parts; compacts lazily."""

  def create_accumulator(self) -> list[SourceSets]:
    return []

  def add_input(self, mutable_accumulator: list[SourceSets],
                element: SourceSets) -> list[SourceSets]:
    mutable_accumulator.append(element)
    if sum(_codes_held(s) for s in mutable_accumulator) > _COMPACT_CODES:
      return [self._compact(mutable_accumulator)]
    return mutable_accumulator

  def merge_accumulators(
      self, accumulators: Iterable[list[SourceSets]]) -> list[SourceSets]:
    merged = [part for acc in accumulators for part in acc]
    return [self._compact(merged)] if len(merged) > 1 else merged

  def extract_output(self, accumulator: list[SourceSets]) -> SourceSets:
    return self._compact(accumulator)

  @staticmethod
  def _compact(parts: Sequence[SourceSets]) -> SourceSets:
    nonkey = np.unique(
        np.concatenate([_EMPTY_CODES, *(p.nonkey for p in parts)]))
    rows = [p.row for p in parts if p.row is not None]
    row = np.unique(np.concatenate(rows)) if rows else None
    return SourceSets(nonkey.astype(np.uint64), row)


# --------------------------------------------------------------------------
# one synthetic batch against the panel and the source sets
# --------------------------------------------------------------------------
@dataclass
class MembershipAcc:  # pylint: disable=too-many-instance-attributes  # one field per count
  """Membership counts of synthetic rows (mergeable: sums, and unions of
  panel positions). `hits_*` are distinct exclusive panel records hit
  (positions in the index's u_r/u_h, split exposed / rest); `near_hits_*`
  distinct exclusive leave-one-out keys hit by a verified near match."""
  n: int = 0
  src_nonkey: int = 0
  src_row: int = 0
  rows_r: int = 0
  rows_h: int = 0
  rows_r_only: int = 0
  rows_h_only: int = 0
  rows_full_r: int = 0
  near_rows_r: int = 0
  near_rows_h: int = 0
  hits_e: np.ndarray = field(default_factory=lambda: _EMPTY_POS)
  hits_r_rest: np.ndarray = field(default_factory=lambda: _EMPTY_POS)
  hits_he: np.ndarray = field(default_factory=lambda: _EMPTY_POS)
  hits_h_rest: np.ndarray = field(default_factory=lambda: _EMPTY_POS)
  near_hits_r: np.ndarray = field(default_factory=lambda: _EMPTY_POS)
  near_hits_h: np.ndarray = field(default_factory=lambda: _EMPTY_POS)

  def merge(self, other: MembershipAcc) -> MembershipAcc:
    values: dict[str, Any] = {}
    for f in fields(self):
      mine, theirs = getattr(self, f.name), getattr(other, f.name)
      values[f.name] = (
          _union(mine, theirs) if isinstance(mine, np.ndarray) else mine +
          theirs)
    return MembershipAcc(**values)


class _Candidate(NamedTuple):
  """A flag before ranking: ordered by (prio, spread, key_text)."""
  prio: int
  spread: int
  key_text: str
  source_set: str
  source_code: int | None
  synthetic_key: dict[str, Any] | None
  distance: float
  detail: dict[str, Any]


def _flag_order(candidate: _Candidate) -> tuple[int, int, str]:
  return candidate.prio, candidate.spread, candidate.key_text


class _Near(NamedTuple):
  rows: np.ndarray  # bool (n,): a verified near match
  column: np.ndarray  # the first dropped column it matched at
  rank: np.ndarray  # the matched row's rank
  hits: np.ndarray  # distinct exclusive key positions hit


def _near(loo: _Loo, keys: np.ndarray, cells: np.ndarray,
          eligible: np.ndarray) -> _Near:
  """Verified leave-one-out matches of a batch's rows against one side."""
  n = len(eligible)
  rows = np.zeros(n, dtype=bool)
  column = np.full(n, -1, dtype=np.int64)
  rank = np.full(n, -1, dtype=np.int64)
  hits = []
  for j in range(keys.shape[1]):
    lo = int(loo.offsets[j])
    pos, found = _lookup(loo.column(j), keys[:, j], eligible)
    cand = np.nonzero(found)[0]
    if not cand.size:
      continue
    matched = loo.first[lo + pos[cand]]
    equal = cells[cand] == loo.cells[matched]
    equal[:, j] = True
    ok = equal.all(axis=1)
    rows_ok, ranks_ok = cand[ok], matched[ok]
    key_pos = lo + pos[rows_ok]
    hits.append(key_pos[loo.only[key_pos]])
    fresh = ~rows[rows_ok]
    rows[rows_ok[fresh]] = True
    column[rows_ok[fresh]] = j
    rank[rows_ok[fresh]] = ranks_ok[fresh]
  merged = np.unique(np.concatenate(hits)) if hits else _EMPTY_POS
  return _Near(rows, column, rank, merged.astype(np.int64))


def _synthetic_key(spec: MembershipSpec, batch: EncodedBatch,
                   i: int) -> tuple[dict[str, Any] | None, str]:
  """Row i's synthetic PK as a dict (INT64 parts as ints) and its JSON."""
  if spec.pk_text is None:
    return None, ""
  key: dict[str, Any] = {}
  for name, slot, is_int in zip(
      spec.pk, spec.pk_text, spec.pk_int, strict=True):
    text = batch.text[slot][i]
    key[name] = int(text) if is_int and text is not None else text
  return key, json.dumps(key, sort_keys=True)


def _spread(spec: MembershipSpec, row_hash: np.ndarray) -> np.ndarray:
  return _mix64(row_hash ^ np.uint64(hash64(_SPREAD_LABEL, spec.salt)))


class _Picked(NamedTuple):
  """The chosen rows' priorities, sets, source-key codes, distance and
  details, aligned with the rows passed to `_candidates`."""
  prio: np.ndarray
  sets: Sequence[str]
  codes: Sequence[int | None]
  distance: float
  details: Sequence[dict[str, Any]]


def _best(rows: np.ndarray, prio: np.ndarray, spread: np.ndarray,
          top_k: int) -> np.ndarray:
  """The order of the `top_k` best `rows` by (priority, spread): only
  those are turned into candidates, so a batch full of copies costs one
  sort, not a Python loop per row."""
  order: np.ndarray = np.lexsort((spread[rows], prio))[:top_k]
  return order


def _candidates(spec: MembershipSpec, batch: EncodedBatch, rows: np.ndarray,
                picked: _Picked, spread: np.ndarray) -> list[_Candidate]:
  """The chosen `rows` as flag candidates."""
  out = []
  for o, i in enumerate(rows.tolist()):
    key, key_text = _synthetic_key(spec, batch, i)
    out.append(
        _Candidate(
            prio=int(picked.prio[o]),
            spread=int(spread[i]),
            key_text=key_text,
            source_set=picked.sets[o],
            source_code=picked.codes[o],
            synthetic_key=key,
            distance=picked.distance,
            detail=picked.details[o]))
  return out


def batch_membership(
    spec: MembershipSpec, index: PanelIndex, sources: SourceSets | None,
    batch: EncodedBatch,
    top_k: int) -> tuple[MembershipAcc, dict[str, list[_Candidate]]]:
  """One synthetic batch's membership counts and its best flag candidates
  per check (module docstring). `sources` is None in keyed-count mode."""
  n = batch.n
  nk, rh = batch.nonkey_hash, batch.row_hash
  acc = MembershipAcc(n=n)
  content = (
      nk != np.uint64(spec.null_content) if spec.nonkey_columns else np.zeros(
          n, dtype=bool))
  src_nk = np.zeros(n, dtype=bool)
  src_row = None
  if sources is not None:
    src_nk = content & _member(sources.nonkey, nk)
    acc.src_nonkey = int(src_nk.sum())
    if spec.keyed and sources.row is not None:
      src_row = _member(sources.row, rh) & (rh != np.uint64(spec.null_row))
    else:
      src_row = src_nk
    acc.src_row = int(src_row.sum())
  pos_r, in_r = _lookup(index.u_r, nk, content & index.exact)
  pos_h, in_h = _lookup(index.u_h, nk, content & index.exact)
  r_class = index.r_class[pos_r] if index.u_r.size else np.zeros(n, np.int8)
  h_class = index.h_class[pos_h] if index.u_h.size else np.zeros(n, np.int8)
  if index.exact:
    acc.rows_r, acc.rows_h = int(in_r.sum()), int(in_h.sum())
    acc.rows_r_only = int((in_r & (r_class != _SHARED)).sum())
    acc.rows_h_only = int((in_h & (h_class != _SHARED)).sum())
    acc.rows_full_r = int((_member(index.r_rows, rh) & content).sum())
    acc.hits_e = _positions(pos_r, in_r & (r_class == _EXPOSED))
    acc.hits_r_rest = _positions(pos_r, in_r & (r_class == _REST))
    acc.hits_he = _positions(pos_h, in_h & (h_class == _EXPOSED))
    acc.hits_h_rest = _positions(pos_h, in_h & (h_class == _REST))
  spread = _spread(spec, rh)
  out = {
      EXACT_COPY:
          _exact_candidates(spec, index, batch, (in_r, pos_r, in_h, pos_h),
                            (src_nk, src_row), spread, top_k)
  }
  if index.near and index.loo_r is not None and index.loo_h is not None:
    keys = loo_hashes(batch.h_nonkey, index.a_nonkey, nk)
    near_r = _near(index.loo_r, keys, batch.h_nonkey, content & ~in_r)
    near_h = _near(index.loo_h, keys, batch.h_nonkey, content & ~in_h)
    acc.near_rows_r, acc.near_rows_h = (int(near_r.rows.sum()),
                                        int(near_h.rows.sum()))
    acc.near_hits_r, acc.near_hits_h = near_r.hits, near_h.hits
    out[NEAR_COPY] = _near_candidates(spec, index, batch, near_r, near_h,
                                      spread, top_k)
  return acc, out


def _exact_candidates(spec: MembershipSpec, index: PanelIndex,
                      batch: EncodedBatch, panel: tuple[np.ndarray, ...],
                      source: tuple[np.ndarray, np.ndarray | None],
                      spread: np.ndarray, top_k: int) -> list[_Candidate]:
  """The best flag candidates among the synthetic rows that copy a record,
  each labelled with the first set of E, R, H, the full source it matches
  (`SOURCE_SETS`)."""
  in_r, pos_r, in_h, pos_h = panel
  src_nk, src_row = source
  rows = np.nonzero(in_r | in_h | src_nk)[0]
  if not rows.size:
    return []
  in_e = np.zeros(len(in_r), dtype=bool)
  if index.u_r.size:  # the record occurs in E: its first rank is in E
    in_e = in_r & (index.r_first[pos_r] < index.e_n)
  prio = np.where(
      in_e, _PRIO_E,
      np.where(in_r, _PRIO_R, np.where(in_h, _PRIO_H, _PRIO_SOURCE)))[rows]
  order = _best(rows, prio, spread, top_k)
  rows, prio = rows[order], prio[order]
  rh = batch.row_hash[rows]
  sets: list[str] = []
  codes: list[int | None] = []
  details: list[dict[str, Any]] = []
  for o, (i, p) in enumerate(zip(rows.tolist(), prio.tolist(), strict=True)):
    sets.append(SOURCE_SETS[p])
    if p in (_PRIO_E, _PRIO_R):
      codes.append(int(index.r_key[index.r_first[pos_r[i]]]))
      full = bool(_member(index.r_rows, rh[o:o + 1])[0])
    elif p == _PRIO_H:
      codes.append(int(index.h_key[index.h_first[pos_h[i]]]))
      full = bool(_member(index.h_rows, rh[o:o + 1])[0])
    else:  # a full-source copy: no panel row to name
      codes.append(None)
      full = src_row is not None and bool(src_row[i])
    details.append({"full_row": full})
  return _candidates(spec, batch, rows, _Picked(prio, sets, codes, 0.0,
                                                details), spread)


def _near_candidates(spec: MembershipSpec, index: PanelIndex,
                     batch: EncodedBatch, near_r: _Near, near_h: _Near,
                     spread: np.ndarray, top_k: int) -> list[_Candidate]:
  """The best flag candidates among the synthetic rows that near-copy a
  panel record (E, then R, then H), each naming the column that differs."""
  rows = np.nonzero(near_r.rows | near_h.rows)[0]
  if not rows.size:
    return []
  from_r = near_r.rows[rows]
  rank = np.where(from_r, near_r.rank[rows], near_h.rank[rows])
  column = np.where(from_r, near_r.column[rows], near_h.column[rows])
  prio = np.where(from_r, np.where(rank < index.e_n, _PRIO_E, _PRIO_R), _PRIO_H)
  order = _best(rows, prio, spread, top_k)
  rows, prio, rank, column = rows[order], prio[order], rank[order], column[
      order]
  sets: list[str] = []
  codes: list[int | None] = []
  details: list[dict[str, Any]] = []
  for p, r, j in zip(
      prio.tolist(), rank.tolist(), column.tolist(), strict=True):
    sets.append(SOURCE_SETS[p])
    codes.append(int((index.h_key if p == _PRIO_H else index.r_key)[r]))
    details.append({"differs_in": spec.nonkey_columns[j]})
  distance = 1.0 / len(spec.nonkey_columns)
  return _candidates(spec, batch, rows,
                     _Picked(prio, sets, codes, distance, details), spread)


# --------------------------------------------------------------------------
# keyed counts (all tables)
# --------------------------------------------------------------------------
class KeyStats(NamedTuple):
  """One table's keyed-count totals (sums over sides and buckets)."""
  n_src: int = 0
  n_syn: int = 0
  null_src: int = 0  # rows with no non-key content (R60)
  null_syn: int = 0
  pk_null: int = 0  # synthetic rows whose PK has a NULL part
  id_null: int = 0
  nk_dup_src: int = 0  # rows in a non-key hash counted twice or more
  nk_dup_syn: int = 0
  nk_match_syn: int = 0  # synthetic rows whose non-key hash the source holds
  pk_dup: int = 0
  pk_distinct: int = 0
  id_dup: int = 0
  id_distinct: int = 0
  row_match_syn: int = 0  # keyed mode: synthetic rows a source row matches

  def plus(self, other: KeyStats) -> KeyStats:
    return KeyStats(*(a + b for a, b in zip(self, other, strict=True)))


@dataclass(frozen=True, eq=False)
class _Codes:
  """Exact counts per code of one keyed-count bucket: sorted codes."""
  codes: np.ndarray
  src: np.ndarray
  syn: np.ndarray


def _bucketed(codes: np.ndarray, bits: int, source: bool) -> Iterator[tuple]:
  """(bucket, _Codes) of one batch's codes on one side."""
  if codes.size == 0:
    return
  unique, counts = np.unique(codes, return_counts=True)
  counts = counts.astype(np.int64)
  zeros = np.zeros(len(unique), dtype=np.int64)
  src, syn = (counts, zeros) if source else (zeros, counts)
  if bits == 0:
    yield 0, _Codes(unique, src, syn)
    return
  bucket = (unique >> np.uint64(64 - bits)).astype(np.int64)
  edges = np.searchsorted(bucket, np.arange(2**bits + 1))
  for b in np.nonzero(np.diff(edges))[0].tolist():
    lo, hi = edges[b], edges[b + 1]
    yield b, _Codes(unique[lo:hi], src[lo:hi], syn[lo:hi])


def key_counts(spec: MembershipSpec,
               batch: EncodedBatch) -> tuple[list[tuple], KeyStats]:
  """One batch's keyed-count elements `((table, kind, bucket), _Codes)`
  and its row totals (module docstring); REFERENCE/HOLDOUT batches count
  nothing."""
  if batch.side not in _COUNTED_SIDES:
    return [], KeyStats()
  source = batch.side is Side.SOURCE
  table, bits = spec.table, spec.bucket_bits
  out: list[tuple] = []
  nulls = 0
  if spec.nonkey_columns:
    nk = batch.nonkey_hash
    content = nk != np.uint64(spec.null_content)
    nulls = int((~content).sum())
    out += [
        ((table, "nk", b), c) for b, c in _bucketed(nk[content], bits, source)
    ]
  pk_null = id_null = 0
  if not source:
    for kind, cols, codes in (("pk", spec.pk, batch.pk_hash),
                              ("id", spec.identity, batch.identity_hash)):
      if not cols:
        continue
      present = codes != np.uint64(NULL_CODE)
      if kind == "pk":
        pk_null = int((~present).sum())
      else:
        id_null = int((~present).sum())
      out += [((table, kind, b), c)
              for b, c in _bucketed(codes[present], bits, source)]
  if spec.source_mode == KEYED_COUNT and spec.keyed:
    rh = batch.row_hash
    out += [
        ((table, "row", b), c)
        for b, c in _bucketed(rh[rh != np.uint64(spec.null_row)], bits, source)
    ]
  n = batch.n
  totals = KeyStats(
      n_src=n if source else 0,
      n_syn=0 if source else n,
      null_src=nulls if source else 0,
      null_syn=0 if source else nulls,
      pk_null=pk_null,
      id_null=id_null)
  return out, totals


class KeyCountsCombineFn(beam.CombineFn):
  """Exact per-code counts of one bucket: `_Codes` parts merged by code
  (lazily compacted, so the combine stays O(total codes))."""

  def create_accumulator(self) -> list[_Codes]:
    return []

  def add_input(self, mutable_accumulator: list[_Codes],
                element: _Codes) -> list[_Codes]:
    mutable_accumulator.append(element)
    if sum(len(p.codes) for p in mutable_accumulator) > _COMPACT_CODES:
      return [_compact_codes(mutable_accumulator)]
    return mutable_accumulator

  def merge_accumulators(self,
                         accumulators: Iterable[list[_Codes]]) -> list[_Codes]:
    merged = [part for acc in accumulators for part in acc]
    return [_compact_codes(merged)] if len(merged) > 1 else merged

  def extract_output(self, accumulator: list[_Codes]) -> _Codes:
    return _compact_codes(accumulator)


def _compact_codes(parts: Sequence[_Codes]) -> _Codes:
  if not parts:
    return _Codes(_EMPTY_CODES, _EMPTY_POS, _EMPTY_POS)
  if len(parts) == 1:
    return parts[0]
  codes = np.concatenate([p.codes for p in parts])
  unique, inverse = np.unique(codes, return_inverse=True)
  src = np.bincount(
      inverse,
      weights=np.concatenate([p.src for p in parts]),
      minlength=len(unique))
  syn = np.bincount(
      inverse,
      weights=np.concatenate([p.syn for p in parts]),
      minlength=len(unique))
  return _Codes(unique, src.astype(np.int64), syn.astype(np.int64))


def _stats_of(kind: str, counts: _Codes) -> KeyStats:
  """One bucket's exact counts as `KeyStats` terms."""
  src, syn = counts.src, counts.syn
  if kind == "nk":
    return KeyStats(
        nk_dup_src=int(src[src >= _DUPLICATED].sum()),
        nk_dup_syn=int(syn[syn >= _DUPLICATED].sum()),
        nk_match_syn=int(syn[src >= 1].sum()))
  if kind == "pk":
    return KeyStats(
        pk_dup=int(syn[syn >= _DUPLICATED].sum()),
        pk_distinct=int((syn > 0).sum()))
  if kind == "id":
    return KeyStats(
        id_dup=int(syn[syn >= _DUPLICATED].sum()),
        id_distinct=int((syn > 0).sum()))
  return KeyStats(row_match_syn=int(syn[src >= 1].sum()))


def _stats_entry(item: tuple[tuple[str, str, int], _Codes]) -> tuple[str, Any]:
  (table, kind, _), counts = item
  return table, _stats_of(kind, counts)


class _SumStatsCombineFn(beam.CombineFn):
  """Sums `KeyStats` (one table's totals)."""

  def create_accumulator(self) -> KeyStats:
    return KeyStats()

  def add_input(self, mutable_accumulator: KeyStats,
                element: KeyStats) -> KeyStats:
    return mutable_accumulator.plus(element)

  def merge_accumulators(self, accumulators: Iterable[KeyStats]) -> KeyStats:
    return functools.reduce(KeyStats.plus, accumulators, KeyStats())

  def extract_output(self, accumulator: KeyStats) -> KeyStats:
    return accumulator


class RowKeysFn(beam.DoFn):
  """`EncodedBatch` → keyed-count elements (main) and row totals (tagged
  `stats`), for every table in `specs`."""

  def __init__(self, specs: Mapping[str, MembershipSpec]):
    super().__init__()
    self._specs = dict(specs)

  def process(self, element: EncodedBatch) -> Iterator[Any]:
    spec = self._specs.get(element.table)
    if spec is None:
      raise ValueError(f"no membership spec for table {element.table!r}")
    counts, totals = key_counts(spec, element)
    yield from counts
    if element.side in _COUNTED_SIDES:
      yield beam.pvalue.TaggedOutput(_STATS, (spec.table, totals))


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
class _Emitter:
  """Collects one table's `MetricValue`s."""

  def __init__(self, spec: MembershipSpec):
    self.spec = spec
    self.rows: list[MetricValue] = []

  def value(self, metric_id: str, value: float | None, **fields_: Any) -> None:
    self.rows.append(
        MetricValue(
            metric_id=metric_id,
            table=self.spec.table,
            value=value,
            method=Method.EXACT,
            encoding_plan_digest=self.spec.encoding_plan_digest,
            **fields_))

  def skip(self, metric_id: str, reason: str, **detail: Any) -> None:
    self.rows.append(
        MetricValue(
            metric_id=metric_id,
            table=self.spec.table,
            value=None,
            method=Method.EXACT,
            encoding_plan_digest=self.spec.encoding_plan_digest,
            detail={
                "reason": reason,
                **detail
            }))


def _share(e: _Emitter, metric_id: str, k: int, n: int, *, n_source: int,
           detail: Mapping[str, Any]) -> None:
  lo, hi = noise.wilson_interval(k, n)
  e.value(
      metric_id,
      k / n,
      ci_low=lo,
      ci_high=hi,
      n_source=n_source,
      n_synthetic=n,
      detail=dict(detail))


def _exact_rates(e: _Emitter, acc: MembershipAcc, keys: KeyStats) -> None:
  spec = e.spec
  side_input = spec.source_mode == SIDE_INPUT
  for metric_id in ("row.exact_match_rate", "row.exact_match_rate_nonkey"):
    nonkey = metric_id.endswith("_nonkey")
    if nonkey and not spec.nonkey_columns:
      e.skip(metric_id, _NO_CONTENT)
      continue
    if keys.n_syn <= 0:
      e.skip(metric_id, _NO_SYNTHETIC)
      continue
    if keys.n_src <= 0:
      e.skip(metric_id, _NO_SOURCE)
      continue
    if nonkey:
      k = acc.src_nonkey if side_input else keys.nk_match_syn
    elif side_input:
      k = acc.src_row
    else:
      k = keys.row_match_syn if spec.keyed else keys.nk_match_syn
    detail: dict[str, Any] = {
        "matches": k,
        "path": spec.source_mode,
        "path_note": spec.source_note,
    }
    if nonkey:
      detail["null_content_rows"] = keys.null_syn
    _share(e, metric_id, k, keys.n_syn, n_source=keys.n_src, detail=detail)


def _rate_ratio_detail(m1: int, t1: int, m2: int, t2: int,
                       names: tuple[str, str]) -> dict[str, Any]:
  ratio, lo, hi = noise.rate_ratio(m1, t1, m2, t2, alpha=LIFT_ALPHA)
  return {
      "lift": ratio,
      "ci_low": lo,
      "ci_high": None if math.isinf(hi) else hi,
      f"events_{names[0]}": m1,
      f"events_{names[1]}": m2,
      f"exposed_{names[0]}": t1,
      f"exposed_{names[1]}": t2,
  }


def _lift(e: _Emitter, metric_id: str, counts: tuple[int, int, int, int],
          names: tuple[str, str], n_syn: int, detail: Mapping[str,
                                                              Any]) -> None:
  m1, t1, m2, t2 = counts
  if t1 <= 0 or t2 <= 0:
    set_a, set_b = ("R", "H") if names[0] == "r" else ("E", "H_E")
    e.skip(metric_id, _NO_EXPOSURE.format(a=set_a, b=set_b), **{
        f"exposed_{names[0]}": t1,
        f"exposed_{names[1]}": t2
    })
    return
  ratio, lo, hi = noise.rate_ratio(m1, t1, m2, t2, alpha=LIFT_ALPHA)
  e.value(
      metric_id,
      ratio,
      ci_low=lo,
      ci_high=None if math.isinf(hi) else hi,
      n_synthetic=n_syn,
      detail={
          f"events_{names[0]}": m1,
          f"events_{names[1]}": m2,
          f"exposed_{names[0]}": t1,
          f"exposed_{names[1]}": t2,
          "unit": "distinct exclusive reference records",
          "alpha": LIFT_ALPHA,
          **detail,
      })


def _panel_reason(spec: MembershipSpec, stats: PanelStats, keys: KeyStats,
                  near: bool) -> str | None:
  if not spec.nonkey_columns:
    return _NO_CONTENT
  if near and len(spec.nonkey_columns) < _MIN_NEAR_COLUMNS:
    return _FEW_COLUMNS
  reason = stats.near_reason if near else stats.lift_reason
  if reason is not None:
    return reason
  if keys.n_syn <= 0:
    return _NO_SYNTHETIC
  return None


def _lifts(e: _Emitter, stats: PanelStats, acc: MembershipAcc,
           keys: KeyStats) -> None:
  reason = _panel_reason(e.spec, stats, keys, near=False)
  if reason is not None:
    for metric_id in ("row.memorization_lift", "row.exposure_lift"):
      e.skip(metric_id, reason)
    return
  e_hits, rest_r = len(acc.hits_e), len(acc.hits_r_rest)
  he_hits, rest_h = len(acc.hits_he), len(acc.hits_h_rest)
  _lift(
      e, "row.memorization_lift",
      (e_hits + rest_r, stats.t_e + stats.t_r_rest, he_hits + rest_h,
       stats.t_he + stats.t_h_rest), ("r", "h"), keys.n_syn, {
           "rows_r": acc.rows_r_only,
           "rows_h": acc.rows_h_only,
           "rows_matching_r": acc.rows_r,
           "rows_matching_h": acc.rows_h,
           "full_row_matches_r": acc.rows_full_r,
       })
  _lift(
      e, "row.exposure_lift", (e_hits, stats.t_e, he_hits, stats.t_he),
      ("e", "he"), keys.n_syn, {
          "e_n":
              stats.e_n,
          "he_n":
              stats.he_n,
          "unexposed":
              _rate_ratio_detail(rest_r, stats.t_r_rest, rest_h, stats.t_h_rest,
                                 ("r", "h")),
      })


def _near_metrics(e: _Emitter, stats: PanelStats, acc: MembershipAcc,
                  keys: KeyStats) -> None:
  reason = _panel_reason(e.spec, stats, keys, near=True)
  if reason is not None:
    for metric_id in ("row.near_match_rate", "row.near_match_lift"):
      e.skip(metric_id, reason)
    return
  _share(
      e,
      "row.near_match_rate",
      acc.near_rows_r,
      keys.n_syn,
      n_source=stats.n_r,
      detail={
          "near_rows_r": acc.near_rows_r,
          "near_rows_h": acc.near_rows_h
      })
  _lift(
      e, "row.near_match_lift",
      (len(acc.near_hits_r), stats.t_near_r, len(
          acc.near_hits_h), stats.t_near_h), ("r", "h"), keys.n_syn, {
              "unit": "distinct exclusive leave-one-out keys",
              "rows_r": acc.near_rows_r,
              "rows_h": acc.near_rows_h,
          })


def _duplicates(e: _Emitter, stats: PanelStats, keys: KeyStats) -> None:
  metric_id = "row.internal_duplicate_excess"
  if not e.spec.nonkey_columns:
    e.skip(metric_id, _NO_CONTENT)
    return
  n_syn, n_src = keys.n_syn - keys.null_syn, keys.n_src - keys.null_src
  if n_syn <= 0 or n_src <= 0:
    side = "synthetic" if n_syn <= 0 else "source"
    e.skip(metric_id, f"no {side} row with non-key content was read")
    return
  d_syn, d_src = keys.nk_dup_syn / n_syn, keys.nk_dup_src / n_src
  lo, hi = noise.newcombe_diff_interval(keys.nk_dup_syn, n_syn, keys.nk_dup_src,
                                        n_src)
  detail: dict[str, Any] = {
      "duplicate_rows_synthetic": keys.nk_dup_syn,
      "duplicate_rows_source": keys.nk_dup_src,
      "null_content_rows_synthetic": keys.null_syn,
      "null_content_rows_source": keys.null_src,
      "counted_on": "full data (Ruling R59)",
  }
  baseline = None
  if stats.r_dup is not None and stats.r_dup[1] > 0:
    baseline = stats.r_dup[0] / stats.r_dup[1] - d_src
  else:
    detail["baseline_reason"] = ("no reference sample (the R panel)"
                                 if stats.r_dup is None else
                                 "no reference row with non-key content")
  e.value(
      metric_id,
      d_syn - d_src,
      source_value=d_src,
      synthetic_value=d_syn,
      baseline_value=baseline,
      ci_low=lo,
      ci_high=hi,
      n_source=n_src,
      n_synthetic=n_syn,
      detail=detail)


def _key_duplicates(e: _Emitter, keys: KeyStats) -> None:
  for metric_id, cols, dup, distinct, nulls, missing in (
      ("table.pk_duplicate_rate", e.spec.pk, keys.pk_dup, keys.pk_distinct,
       keys.pk_null, _NO_PK),
      ("table.identity_duplicate_rate", e.spec.identity, keys.id_dup,
       keys.id_distinct, keys.id_null, _NO_IDENTITY),
  ):
    if not cols:
      e.skip(metric_id, missing)
      continue
    if keys.n_syn <= 0:
      e.skip(metric_id, _NO_SYNTHETIC)
      continue
    e.value(
        metric_id,
        dup / keys.n_syn,
        n_synthetic=keys.n_syn,
        detail={
            "duplicate_rows": dup,
            "distinct_keys": distinct,
            "null_key_rows": nulls,
            "key_columns": list(cols),
            "null_key_note": _NULL_KEY_NOTE,
        })


def table_outputs(spec: MembershipSpec, stats: PanelStats,
                  acc: MembershipAcc | None,
                  keys: KeyStats | None) -> list[MetricValue]:
  """One table's membership metrics: a row per owned id, a value or
  `not_evaluated` with a reason, in `OWNED_METRIC_IDS` order."""
  acc = acc or MembershipAcc()
  keys = keys or KeyStats()
  e = _Emitter(spec)
  _exact_rates(e, acc, keys)
  _lifts(e, stats, acc, keys)
  _near_metrics(e, stats, acc, keys)
  _duplicates(e, stats, keys)
  _key_duplicates(e, keys)
  order = {metric_id: i for i, metric_id in enumerate(OWNED_METRIC_IDS)}
  return sorted(e.rows, key=lambda mv: order[mv.metric_id])


# --------------------------------------------------------------------------
# flags
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RowFlag:  # pylint: disable=too-many-instance-attributes  # the flag table's fields
  """One `evaluation_row_flags` row before the run's ids are attached
  (`flag_row`). Keys only: the synthetic PK and a keyed label of the
  matched source key; never an attribute value."""
  table: str
  check: str
  rank: int
  synthetic_key: Mapping[str, Any] | None
  source_key_hash: str | None
  source_set: str
  distance: float | None
  score: float | None
  detail: Mapping[str, Any] = field(default_factory=dict)


def _flags(item: tuple[tuple[str, str], Iterable[_Candidate]],
           label_key: bytes) -> Iterator[RowFlag]:
  (table, check), candidates = item
  if not isinstance(label_key, bytes) or not label_key:
    raise ValueError("the label key side input must be non-empty bytes (R64)")
  for rank, c in enumerate(sorted(candidates, key=_flag_order), start=1):
    yield RowFlag(
        table=table,
        check=check,
        rank=rank,
        synthetic_key=c.synthetic_key,
        source_key_hash=(None if c.source_code is None else hashed_label(
            c.source_code, key=label_key)),
        source_set=c.source_set,
        distance=c.distance,
        score=1.0 - c.distance,
        detail=c.detail)


def _utc(evaluated_at: datetime | str) -> str:
  """`evaluated_at` as UTC ISO-8601 with microseconds (naive = UTC), as
  `scoring.to_metric_row` writes it."""
  if isinstance(evaluated_at, str):
    evaluated_at = datetime.fromisoformat(evaluated_at)
  return str(canonical_value(evaluated_at))


def flag_row(flag: RowFlag, *, evaluation_id: str,
             evaluated_at: datetime | str) -> dict[str, Any]:
  """One `evaluation_row_flags` row: exactly its fields, in schema order,
  JSON-safe. `source_key` is always NULL (hashed source keys, D6/R64).

  Raises:
    ValueError: `evaluated_at` is a string that is not ISO-8601.
  """
  row = {
      "evaluation_id": evaluation_id,
      "evaluated_at": _utc(evaluated_at),
      "table_name": flag.table,
      "check": flag.check,
      "rank": flag.rank,
      "synthetic_key":
          (None if flag.synthetic_key is None else dict(flag.synthetic_key)),
      "source_key_hash": flag.source_key_hash,
      "source_key": None,
      "source_set": flag.source_set,
      "distance": flag.distance,
      "score": flag.score,
      "detail": dict(flag.detail) or None,
  }
  safe: dict[str, Any] = json_safe(row)
  return safe


# --------------------------------------------------------------------------
# in process (the reference implementation the Beam path is held to)
# --------------------------------------------------------------------------
class MembershipResult(NamedTuple):
  metrics: list[MetricValue]
  flags: list[RowFlag]


def membership_outputs(
    table: TablePlan,
    batches: Iterable[EncodedBatch],
    *,
    salt: str,
    label_key: bytes,
    row_flags_top_k: int = 100,
    panel_max_bytes: int = PANEL_SET_MAX_BYTES,
    source_set_max_bytes: int = SOURCE_SET_MAX_BYTES) -> MembershipResult:
  """The whole membership pass of one table in one process."""
  spec = MembershipSpec.from_table(
      table,
      salt=salt,
      panel_max_bytes=panel_max_bytes,
      source_set_max_bytes=source_set_max_bytes)
  index = PanelIndex.build(spec, PanelRefs.from_table(table, spec))
  batches = [b for b in batches if b.table == table.name]
  sources = None
  if spec.source_mode == SIDE_INPUT:
    combine = SourceSetsCombineFn()
    parts = combine.create_accumulator()
    for batch in batches:
      if batch.side is Side.SOURCE:
        parts = combine.add_input(parts, source_hashes(spec, batch))
    sources = combine.extract_output(parts)
  keys = KeyStats()
  buckets: dict[tuple, list[_Codes]] = {}
  for batch in batches:
    elements, totals = key_counts(spec, batch)
    keys = keys.plus(totals)
    for key, part in elements:
      buckets.setdefault(key, []).append(part)
  for (_, kind, _), parts_ in sorted(buckets.items(), key=lambda kv: kv[0]):
    keys = keys.plus(_stats_of(kind, _compact_codes(parts_)))
  acc = MembershipAcc()
  best: dict[str, list[_Candidate]] = {}
  for batch in batches:
    if batch.side is not Side.SYNTHETIC:
      continue
    part, candidates = batch_membership(spec, index, sources, batch,
                                        row_flags_top_k)
    acc = acc.merge(part)
    for check, items in candidates.items():
      best[check] = sorted([*best.get(check, []), *items],
                           key=_flag_order)[:row_flags_top_k]
  flags = [
      flag for check in sorted(best)
      for flag in _flags(((table.name, check), best[check]), label_key)
  ]
  return MembershipResult(table_outputs(spec, index.stats, acc, keys), flags)


# --------------------------------------------------------------------------
# Beam
# --------------------------------------------------------------------------
class MembershipFn(beam.DoFn):
  """Synthetic `EncodedBatch` → `(table, MembershipAcc)` and tagged flag
  candidates `((table, check), candidate)`. The panel index is built once
  per worker from the panel side input (`Shared`, tagged by its content
  token) and kept alive by the DoFn; other sides pass through untouched."""

  def __init__(self, spec: MembershipSpec, top_k: int):
    super().__init__()
    self._spec = spec
    self._top_k = top_k
    self._shared = Shared()
    self._index: PanelIndex | None = None

  def process(self,
              element: EncodedBatch,
              refs: PanelRefs,
              sources: SourceSets | None = None) -> Iterator[Any]:
    if element.side is not Side.SYNTHETIC:
      return
    spec = self._spec
    self._index = self._shared.acquire(
        functools.partial(PanelIndex.build, spec, refs), tag=refs.token)
    acc, candidates = batch_membership(spec, self._index, sources, element,
                                       self._top_k)
    yield spec.table, acc
    for check, items in candidates.items():
      for candidate in items:
        yield beam.pvalue.TaggedOutput(_FLAGS, ((spec.table, check), candidate))


class _SourceHashesFn(beam.DoFn):

  def __init__(self, spec: MembershipSpec):
    super().__init__()
    self._spec = spec

  def process(self, element: EncodedBatch) -> Iterator[SourceSets]:
    if element.side is Side.SOURCE:
      yield source_hashes(self._spec, element)


class MembershipCombineFn(beam.CombineFn):
  """Merges `MembershipAcc`s (sums and position unions)."""

  def create_accumulator(self) -> MembershipAcc:
    return MembershipAcc()

  def add_input(self, mutable_accumulator: MembershipAcc,
                element: MembershipAcc) -> MembershipAcc:
    return mutable_accumulator.merge(element)

  def merge_accumulators(
      self, accumulators: Iterable[MembershipAcc]) -> MembershipAcc:
    return functools.reduce(MembershipAcc.merge, accumulators, MembershipAcc())

  def extract_output(self, accumulator: MembershipAcc) -> MembershipAcc:
    return accumulator


class _ByTable(beam.PartitionFn):
  """The partition of a batch: its table's index in `names`."""

  def __init__(self, names: Sequence[str]):
    super().__init__()
    self._names = tuple(names)

  def partition_for(self, element: Any, num_partitions: int, *args: Any,
                    **kwargs: Any) -> int:
    del args, kwargs  # no extra partition arguments
    if element.table not in self._names[:num_partitions]:
      raise ValueError(f"no membership spec for table {element.table!r}")
    return self._names.index(element.table)


def _tagged(item: tuple[str, Any], tag: str) -> tuple[str, tuple[str, Any]]:
  table, value = item
  return table, (tag, value)


def _emit_table(item: tuple[str, Iterable[tuple[str, Any]]],
                specs: Mapping[str, MembershipSpec],
                stats: Mapping[str, PanelStats]) -> Iterator[MetricValue]:
  table, entries = item
  acc, keys = None, None
  for tag, value in entries:
    if tag == _ACC:
      acc = value
    elif tag == _KEYS:
      keys = value
  yield from table_outputs(specs[table], stats[table], acc, keys)


class Membership(beam.PTransform):
  """`PCollection[EncodedBatch]` (every table, every side) → `{"metrics":
  PCollection[MetricValue], "flags": PCollection[RowFlag]}` (module
  docstring).

  `label_key` is the one-element key PCollection `beam.label_key.LabelKey`
  makes on a worker, read as a side input when the flags are labelled, so
  the key never enters the job graph (Rulings R64, R68). `salt` must be
  the salt the batches were encoded with (the plan's). The panel hashes
  are built here, on the driver, and shipped as one side input per table;
  only each table's slim `MembershipSpec` is pickled into the DoFns.

  Raises:
    TypeError: `label_key` is not a PCollection (bytes here would be
      pickled into the graph).
  """

  def __init__(self,
               tables: Sequence[TablePlan],
               *,
               salt: str,
               label_key: beam.PCollection,
               row_flags_top_k: int = 100,
               panel_max_bytes: int = PANEL_SET_MAX_BYTES,
               source_set_max_bytes: int = SOURCE_SET_MAX_BYTES):
    super().__init__()
    if not isinstance(label_key, beam.PCollection):
      raise TypeError("label_key must be the LabelKey PCollection, never "
                      "bytes: a constructor argument is pickled into the job "
                      "graph (Ruling R68)")
    self._label_key = label_key
    self._top_k = row_flags_top_k
    self._names = tuple(table.name for table in tables)
    self._specs = {
        table.name:
            MembershipSpec.from_table(
                table,
                salt=salt,
                panel_max_bytes=panel_max_bytes,
                source_set_max_bytes=source_set_max_bytes) for table in tables
    }
    self._refs = {
        table.name: PanelRefs.from_table(table, self._specs[table.name])
        for table in tables
    }
    self._stats = {
        name: PanelIndex.build(self._specs[name], refs).stats
        for name, refs in self._refs.items()
    }

  def expand(self, input_or_inputs: beam.PCollection) -> dict[str, Any]:
    batches = input_or_inputs
    p = batches.pipeline
    names, specs = self._names, self._specs
    keyed = batches | "RowKeys" >> beam.ParDo(RowKeysFn(specs)).with_outputs(
        _STATS, main=_COUNTS)
    key_stats = ((
        keyed[_COUNTS]
        | "SumKeys" >> beam.CombinePerKey(KeyCountsCombineFn())
        | "KeyStats" >> beam.Map(_stats_entry),
        keyed[_STATS],
    )
                 | "FlattenKeyStats" >> beam.Flatten()
                 | "SumKeyStats" >> beam.CombinePerKey(_SumStatsCombineFn()))
    parts = batches | "ByTable" >> beam.Partition(_ByTable(names), len(names))
    accs, candidates = [], []
    for i, name in enumerate(names):
      spec = specs[name]
      refs = beam.pvalue.AsSingleton(
          p | f"Panel[{name}]" >> beam.Create([self._refs[name]]))
      side_inputs: list[Any] = [refs]
      if spec.source_mode == SIDE_INPUT:
        sets = (
            parts[i]
            | f"SourceHashes[{name}]" >> beam.ParDo(_SourceHashesFn(spec))
            | f"SourceSets[{name}]" >> beam.CombineGlobally(
                SourceSetsCombineFn()))
        side_inputs.append(beam.pvalue.AsSingleton(sets))
      out = parts[i] | f"Membership[{name}]" >> beam.ParDo(
          MembershipFn(spec, self._top_k), *side_inputs).with_outputs(
              _FLAGS, main=_ACCS)
      accs.append(out[_ACCS])
      candidates.append(out[_FLAGS])
    combined = (
        accs
        | "FlattenAccs" >> beam.Flatten()
        | "SumAccs" >> beam.CombinePerKey(MembershipCombineFn()))
    flags = (
        candidates
        | "FlattenCandidates" >> beam.Flatten()
        | "TopFlags" >> beam.combiners.Top.PerKey(
            self._top_k, key=_flag_order, reverse=True)
        | "Flags" >> beam.FlatMap(_flags,
                                  beam.pvalue.AsSingleton(self._label_key)))
    seeds = p | "Seeds" >> beam.Create([(name, (_SEED, None))
                                        for name in names])
    metrics = ((
        combined | "TagAccs" >> beam.Map(_tagged, _ACC),
        key_stats | "TagKeys" >> beam.Map(_tagged, _KEYS),
        seeds,
    )
               | "Parts" >> beam.Flatten()
               | "ByTableParts" >> beam.GroupByKey()
               | "Emit" >> beam.FlatMap(_emit_table, specs, self._stats))
    return {"metrics": metrics, "flags": flags}
