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
"""Nearest-neighbour privacy and detection over deterministic samples:
`row.dcr_train_holdout_share`, `row.dcr_p5_ratio`, `row.nndr_p5_ratio`,
`row.density`, `row.coverage`, `table.detection_auc` and
`table.pmse_ratio`, their profiles and their row flags. The maths is
`stats.privacy` and `stats.detection`; this module samples, fans the
Gower search out and assembles the rows.

    EncodedBatch (every table, every side) ─ Partition(table) ─► per table:
      SamplePartsFn   priority = SplitMix64(row_hash, salt); rows under the
        │             plan-sized prefilter rate ─► (side, SamplePart)
        ▼             (+ one empty seed per side)
      CombinePerKey(side, SampleCombineFn)   BottomK(k), k = max(privacy,
        │                                    detection sample rows), with
        │                                    every key's exact multiplicity
        ├─► ToDict ─► DetectionFn ◄── PanelInputs (AsSingleton)
        │     ONE element, so one worker: featurize ─► c2st_auc (DeLong)
        │     ─► pmse_ratio ─► table.detection_auc, table.pmse_ratio,
        │     roc_curve, detectable flags
        └─► nn_records (synthetic: priority order, weight = multiplicity,
              │         cut at privacy_sample_rows ROWS)
              Reshuffle ─► BatchElements(≤ 4096) ─► GowerNNFn ◄── PanelInputs
              │   index: R/H Gower blocks + R source codes, built once per
              │   worker (`Shared`, tagged by the feature set and panel)
              CombineGlobally(NNMergeFn) ─► PrivacyEmitFn ◄── PanelInputs,
                  label key (AsSingleton), side row counts, failures
                  holdout_mass ─► summarize_nn ─► the five row metrics,
                  dcr_hist / nndr_hist, nearest_record flags

Sampling. Both sides are sampled by the same pure priority of the row
hash (`beam.encode`: salted, over every plan column), so a row the
synthetic side copies whole draws its source twin's priority
(coordinated bottom-k; Cohen & Kaplan, 2007). `sampling.reservoir.
BottomK` keeps the k smallest priorities with each key's EXACT
multiplicity (Ruling R34; the tie-break of two payloads under one key is
their canonical JSON, R35). The priority is SplitMix64's finaliser of
the row hash XOR a salt word (Steele, Lea & Flood, 2014): vectorised,
and a pure function of (salt, key), which is all BottomK's exactness
needs. Before any row reaches Python, a batch keeps only the rows whose
priority lies below q · 2^64, q = min(1, (2k + 100) / N) with N the
plan's rows read on that side. About 2k + 100 rows are expected to
pass, and the samples only ever read the lowest priorities up to k ROWS
(keys in priority order, each with its copies), so while at least k rows
pass — all but a vanishing binomial tail — the kept samples equal an
unfiltered bottom-k's, while the shuffle carries about 2k payloads per
side instead of up to k per bundle. Should fewer rows pass (a stale row
count, or one key holding most of the table), the sample is still an
equal-probability sample of keys, only shorter; `detail.sample_rows`
says how many rows it held. Every occurrence of a key shares its
priority, so the prefilter keeps or drops all its copies together and
the multiplicities stay exact.

Weights. The synthetic rows the privacy metrics see are the sampled keys
in priority order, each repeated by its multiplicity, cut at
`privacy_sample_rows` rows (the last key partly): an equal-probability
sample of rows whose copies stay together. Distances are computed once
per key and repeated by its weight, so a row held c times weighs c, not
1 (R34). The holdout share is rebuilt from the pooled nearest-neighbour
masses, `nn_mass[:n].sum() / n_syn` (R29, whose `permutation_se` assumes
|R| = |H| = n). Density and coverage take the first min(|R|, n_syn) of
those rows in priority order — the BottomK hash, so the NNMerge result
never depends on arrival order — against R at the same n (D5).

The feature space is the plan's (`stats.privacy.GowerSpace`): the
non-key numeric and temporal columns through their SOURCE quantile grid
(UNIX microseconds for temporal columns, Ruling R54), the non-key
categorical, boolean, text and identifier columns as `hash64` codes. Keys
(primary, foreign and identity columns) and nested columns are never
features — the membership pass's "content". A numeric column the plan
holds no source grid for (an all-NULL source column) is left out and
named in `detail.excluded`. The encoder reads an absent column as NULL,
so every panel row must carry every plan column: a row that does not is
refused with the column names, never read as NULL.

R and H (the panel, D3) arrive as a side input (`PanelInputs`, with the
Gower space), are encoded once per worker behind
`apache_beam.utils.shared.Shared` — on the first `process()` call, since
a side input cannot be read in `setup()` — and the index is kept alive
by the DoFn. The five row metrics are `not_evaluated`, with the reason,
when the panel is missing, not verified (`UNVERIFIED_REASON`: R is not
known to be what the generator read), |R| ≠ |H| (the holdout test and
its permutation variance need equal halves), or R or H holds fewer than
`MIN_PANEL_ROWS`. A 5th-percentile ratio whose holdout percentile is 0 —
holdout rows duplicating reference records, as on a lattice — is
`not_evaluated` (`HOLDOUT_P5_ZERO`, `HOLDOUT_NNDR_P5_ZERO`), never +inf.
`baseline_value` (D4) of density and coverage is R read as the synthetic
side against H as the real one, at the same n.

Detection runs on ONE element per table (both samples), on one worker.
`featurize` reads the payload cells exactly as it reads the client's
rows: numeric and temporal cells as the encoder's floats (micros),
booleans as `bool`, strings as the text block holds them (`hash64`
canonicalises BYTES and its base64 text alike). Keys (primary and foreign)
are never features; identity columns are text features. A text or
identifier column's head masks are the SOURCE sample's `shape_of` masks
holding at least 2 % of its non-empty values (ADR 0026's floor), most
frequent first — never the synthetic side's. Memory: two samples of at
most `detection_sample_rows` rows as Python tuples (about 60 B a cell),
the float64 feature matrix of 2n x F features (F = numeric + categorical
+ 7 per text column; 40 MB at n = 50,000 and F = 50), the gradient
boosting's binned copy (uint8, an eighth of it) and the logistic design
(CSR) — well under a gigabyte at the defaults. `baseline_value` is the
same test with R in the synthetic class (D4). `table.pmse_ratio` always
carries `detail["ceiling"]` = N / (k - 1) (R33: scoring marks it
not_evaluated when the ceiling sits below the fail threshold). The seed
is a function of the salt.

Flags carry keys only, never an attribute value (D6, R64/R68):

    nearest_record   the synthetic rows nearest to a reference record,
                     ascending Gower distance (priority order breaks
                     ties); `source_key_hash` is the keyed label of the
                     matched R record's code (its PK hash, else identity
                     hash, else its record hash), `distance`/`score = 1 -
                     distance`; detail: holdout distance, NNDR,
                     multiplicity
    detectable       only when detection is significant (DeLong ci_low >
                     0.5): the synthetic rows the classifier scores most
                     surely synthetic (out of fold), one per sampled key;
                     `score` = that probability

`synthetic_key` is the synthetic row's PK tuple, else its identity tuple
(the membership convention; NULL for a table with neither). At most
`row_flags_top_k` of each.

Sampled mode (Ruling R72). Every metric here is a sample estimate
(`method = sample`; `sample_rate` = the share of the synthetic table the
metric saw, the sampled-mode read rate included). None needs every row
of a side, so none is withheld; the read rate is also in
`detail.sample_rate`, as the membership pass writes it.

Failures stay per table and per block. A table whose spec cannot be
built on the driver writes `not_evaluated` rows with the reason for
every owned id; a Gower space that cannot be built, or a worker error in
the sampling (both blocks) or the nearest-neighbour search (privacy
only), makes those metrics `not_evaluated` with the reason and drops
their flags; the other block, tables and the run carry on.

References (author-year, R22): Platzer & Reutterer (2021); Gower (1971);
Naeem et al. (2020); Giomi et al. (2023); Lopez-Paz & Oquab (2017);
DeLong, DeLong & Clarke-Pearson (1988); Woo et al. (2009); Snoke et al.
(2018); Cohen & Kaplan (2007); Steele, Lea & Flood (2014).

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import apache_beam as beam
import numpy as np
from apache_beam.utils.shared import Shared

from sdfb_evaluation.beam.encode import BatchEncoder, BatchLayout, EncodedBatch
from sdfb_evaluation.beam.membership import (
    TABLE_ERRORS,
    UNVERIFIED_REASON,
    RowFlag,
)
from sdfb_evaluation.canonical import NULL_CODE, hash64, hashed_label
from sdfb_evaluation.sampling.reservoir import BottomK
from sdfb_evaluation.stats.detection import (
    DetectionColumn,
    c2st_auc,
    featurize,
    pmse_ratio,
    roc_points,
)
from sdfb_evaluation.stats.privacy import (
    GowerSpace,
    NNPrivacyResult,
    density_coverage,
    gower_knn,
    holdout_mass,
    nndr,
    summarize_nn,
)
from sdfb_evaluation.stats.shapes import shape_of
from sdfb_evaluation.types import (
    ColumnKind,
    Method,
    MetricValue,
    ProfileValue,
    Side,
)

if TYPE_CHECKING:
  from sdfb_evaluation.context.plan import TablePlan

__all__ = [
    "DENSITY_K",
    "DETECTABLE",
    "DETECTION_FOLDS",
    "DETECTION_METRIC_IDS",
    "HOLDOUT_NNDR_P5_ZERO",
    "HOLDOUT_P5_ZERO",
    "MIN_PANEL_ROWS",
    "NEAREST_RECORD",
    "NN_BATCH_ROWS",
    "OWNED_METRIC_IDS",
    "PRIVACY_METRIC_IDS",
    "TABLE_ERRORS",
    "UNVERIFIED_REASON",
    "GowerNNFn",
    "NNIndex",
    "NNMergeFn",
    "NNPart",
    "NNRecord",
    "PanelInputs",
    "Privacy",
    "PrivacyResult",
    "PrivacySpec",
    "Sample",
    "SampleCombineFn",
    "SamplePart",
    "build_nn_index",
    "detection_block",
    "detection_columns",
    "gower_part",
    "merge_nn",
    "nn_records",
    "privacy_block",
    "privacy_outputs",
    "sample_parts",
    "sample_priority",
]

PRIVACY_METRIC_IDS: tuple[str, ...] = (
    "row.dcr_train_holdout_share",
    "row.dcr_p5_ratio",
    "row.nndr_p5_ratio",
    "row.density",
    "row.coverage",
)
DETECTION_METRIC_IDS: tuple[str, ...] = (
    "table.detection_auc",
    "table.pmse_ratio",
)
OWNED_METRIC_IDS: tuple[str, ...] = PRIVACY_METRIC_IDS + DETECTION_METRIC_IDS

NEAREST_RECORD, DETECTABLE = "nearest_record", "detectable"
MIN_PANEL_ROWS = 2000  # R and H each: below it the tails are too thin
NN_BATCH_ROWS = 4096  # synthetic keys per nearest-neighbour batch
DENSITY_K = 5  # Naeem et al.'s k (the catalogue's)
DETECTION_FOLDS = 5
HOLDOUT_P5_ZERO = "holdout p5 distance is 0 (exact duplicates in the holdout)"
HOLDOUT_NNDR_P5_ZERO = "holdout p5 NNDR is 0 (exact duplicates in the holdout)"

_SOURCE, _SYNTHETIC = Side.SOURCE.value, Side.SYNTHETIC.value
_SAMPLED_SIDES = (_SOURCE, _SYNTHETIC)
_NUMERIC_KINDS = frozenset({ColumnKind.NUMERIC, ColumnKind.TEMPORAL})
_CODED_KINDS = frozenset({ColumnKind.CATEGORICAL, ColumnKind.BOOLEAN})
_GOWER_CODED = frozenset({
    ColumnKind.CATEGORICAL, ColumnKind.BOOLEAN, ColumnKind.TEXT,
    ColumnKind.IDENTIFIER
})
_INT_TYPES = frozenset({"INT64", "INTEGER"})
_NUM, _BOOL, _TEXT = "num", "bool", "text"  # a payload cell's batch block
# The prefilter keeps about 2k + 100 rows per side; the k rows the samples
# read lie below its rate unless the kept count falls ~sqrt(k / 2) standard
# deviations short (module docstring).
_PREFILTER_FACTOR = 2
_PREFILTER_SLACK = 100
_TWO_64 = 2**64
_SPLITMIX = (np.uint64(0x9E3779B97F4A7C15), np.uint64(0xBF58476D1CE4E5B9),
             np.uint64(0x94D049BB133111EB))
_PRIORITY_LABEL = "sdfb:privacy-sample"
_SEED_LABEL = "sdfb:detection-seed"
_SEED_MODULUS = 2**31
_HEAD_MASK_SHARE = 0.02  # ADR 0026's head-mask floor
_MAX_HEAD_MASKS = 254  # detection's codebook limit
_REASON_CHARS = 300
_ROC_POINTS = 101
_CHANCE = 0.5
_TABLE, _PRIVACY = "table", "privacy"  # a failure's scope
_PARTS, _FAILED = "parts", "failed"
_METRICS, _PROFILES, _FLAGS = "metrics", "profiles", "flags"

_NO_PANEL = "no reference panel (R/H) was planned for this table"
_NO_FEATURES = ("no non-key feature column (every column is a key, nested, "
                "or numeric without a source grid): there is no Gower space")
_NO_SYNTHETIC = "no synthetic rows were read"
_NO_SOURCE = "no source rows were read"
_FEW_AT_EQUAL_N = ("fewer than k + 1 = {need} rows at equal n "
                   "(min(|R|, synthetic rows) = {n})")


def _failure(what: str, exc: BaseException) -> str:
  """A block's failure reason: the error class and its message (bounded;
  encoder and numpy messages name columns and types, never values)."""
  text = (f"{what} could not be computed for this table "
          f"({type(exc).__name__}: {exc})")
  return text[:_REASON_CHARS]


def _checked_key(label_key: Any) -> bytes:
  if not isinstance(label_key, bytes) or not label_key:
    raise ValueError("label_key must be non-empty bytes (Ruling R64)")
  return label_key


def _positive(name: str, value: Any) -> int:
  if isinstance(value, bool) or not isinstance(value, int) or value < 1:
    raise ValueError(f"{name}: expected an int >= 1, got {value!r}")
  return value


def sample_priority(row_hash: np.ndarray, salt: str) -> np.ndarray:
  """Each row's uint64 sampling priority: SplitMix64's finaliser of its
  row hash XOR a salt word — a pure function of (salt, key), as BottomK's
  exact multiplicities need (module docstring)."""
  word = np.uint64(hash64(_PRIORITY_LABEL, salt))
  with np.errstate(over="ignore"):
    z = (np.asarray(row_hash, dtype=np.uint64) ^ word) + _SPLITMIX[0]
    z = (z ^ (z >> np.uint64(30))) * _SPLITMIX[1]
    z = (z ^ (z >> np.uint64(27))) * _SPLITMIX[2]
    out: np.ndarray = z ^ (z >> np.uint64(31))
  return out


def _prefilter_limit(k: int, rows: float) -> int | None:
  """The priority below which a batch keeps a row, or None (keep all) when
  the side's row count is unknown or the rate reaches 1."""
  if rows <= 0:
    return None
  rate = (_PREFILTER_FACTOR * k + _PREFILTER_SLACK) / rows
  if rate >= 1.0:
    return None
  return int(rate * _TWO_64)


# --------------------------------------------------------------------------
# the per-table spec (driver-built, pickled into the DoFns: slim)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PrivacySpec:  # pylint: disable=too-many-instance-attributes  # one field per fact the passes read
  """What the privacy and detection passes need of one `TablePlan` —
  names, sizes and rates, never grids or panel rows (those travel in the
  `PanelInputs` side input).

  `sample_columns` are the columns a sampled row carries (every non-key,
  non-nested column, plus the flag handle); `cells` says where each is
  read in an `EncodedBatch` (`(block, index)`); `handle` is the flag
  handle (the PK, else the identity); `limits` are the (source,
  synthetic) prefilter priorities (None: keep every row).
  """
  table: str
  encoding_plan_digest: str
  salt: str
  layout: BatchLayout
  sample_columns: tuple[str, ...]
  cells: tuple[tuple[str, int], ...]
  handle: tuple[str, ...]
  handle_int: tuple[bool, ...]
  privacy_rows: int
  detection_rows: int
  top_k: int
  limits: tuple[int | None, int | None]
  seed: int
  rate_source: float = 1.0
  rate_synthetic: float = 1.0

  @property
  def sample_k(self) -> int:
    """The BottomK size of both sides: the larger of the two samples (a
    bottom-k's prefix is the smaller bottom-k)."""
    return max(self.privacy_rows, self.detection_rows)

  @classmethod
  def from_table(cls,
                 table: TablePlan,
                 *,
                 salt: str,
                 privacy_sample_rows: int = 50_000,
                 detection_sample_rows: int = 50_000,
                 row_flags_top_k: int = 100) -> PrivacySpec:
    """Raises:
      ValueError: a sample size or top-k below 1, or a key column the
        plan does not hold (`BatchLayout.from_table`).
    """
    privacy_rows = _positive("privacy_sample_rows", privacy_sample_rows)
    detection_rows = _positive("detection_sample_rows", detection_sample_rows)
    top_k = _positive("row_flags_top_k", row_flags_top_k)
    layout = BatchLayout.from_table(table)
    keys = _detection_keys(table)
    handle = layout.pk or layout.identity
    kinds = dict(zip(layout.columns, layout.kinds, strict=True))
    columns = tuple(name for name in layout.columns
                    if kinds[name] is not ColumnKind.NESTED and
                    (name not in keys or name in handle))
    bq_type = {c.name: c.bq_type.upper() for c in table.columns}
    k = max(privacy_rows, detection_rows)
    rows_source, rows_synthetic = table.rows_read
    return cls(
        table=table.name,
        encoding_plan_digest=table.encoding_plan_digest,
        salt=salt,
        layout=layout,
        sample_columns=columns,
        cells=tuple(_cell_of(layout, name, kinds[name]) for name in columns),
        handle=handle,
        handle_int=tuple(bq_type[c] in _INT_TYPES for c in handle),
        privacy_rows=privacy_rows,
        detection_rows=detection_rows,
        top_k=top_k,
        limits=(_prefilter_limit(k, rows_source),
                _prefilter_limit(k, rows_synthetic)),
        seed=hash64(_SEED_LABEL, salt) % _SEED_MODULUS,
        rate_source=_rate(table.sample_rate_source),
        rate_synthetic=_rate(table.sample_rate_synthetic))


def _rate(rate: float | None) -> float:
  return 1.0 if rate is None or rate >= 1.0 else float(rate)


def _detection_keys(table: TablePlan) -> frozenset[str]:
  """The key columns detection never reads: flagged keys, the PK and every
  FK column (a synthetic FK points at synthetic parents, so it would
  detect the tables trivially). Identity columns are not keys here."""
  keys = {c.name for c in table.columns if c.is_key} | set(table.pk)
  keys |= {c for edge in table.edges for c in edge.cols}
  return frozenset(keys)


def _cell_of(layout: BatchLayout, name: str,
             kind: ColumnKind) -> tuple[str, int]:
  """Where a column's cell is read in an `EncodedBatch`."""
  if kind in _NUMERIC_KINDS:
    return _NUM, layout.num_columns.index(name)
  if kind is ColumnKind.BOOLEAN:
    return _BOOL, layout.num_columns.index(name)
  return _TEXT, layout.text_columns.index(name)


def _handle(spec: PrivacySpec, values: Sequence[Any]) -> dict[str, Any] | None:
  """A sampled row's flag handle (PK, else identity) — INT64 parts as ints."""
  if not spec.handle:
    return None
  position = {name: i for i, name in enumerate(spec.sample_columns)}
  key: dict[str, Any] = {}
  for name, is_int in zip(spec.handle, spec.handle_int, strict=True):
    value = values[position[name]]
    key[name] = int(value) if is_int and value is not None else value
  return key


# --------------------------------------------------------------------------
# the side input: Gower space, detection plan columns, the panel rows
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DetectionPlan:
  """One plan column's detection facts (built into a `DetectionColumn`
  once the source sample's head masks are known)."""
  name: str
  kind: ColumnKind
  is_key: bool
  grid: tuple[float, ...] | None
  dictionary: tuple[int, ...] | None


@dataclass(frozen=True, eq=False)
class PanelInputs:  # pylint: disable=too-many-instance-attributes  # one field per side-input fact
  """The side input of one table (module docstring): the Gower space, the
  detection plan columns and the panel rows projected to the plan's
  columns, plus why the nearest-neighbour metrics cannot run
  (`privacy_reason`, None when they can). `token` digests the whole
  content: the tag of the worker's index.
  """
  space: GowerSpace | None
  excluded: tuple[str, ...]
  privacy_reason: str | None
  columns: tuple[DetectionPlan, ...]
  r_rows: tuple[dict, ...]
  h_rows: tuple[dict, ...]
  n_panel: int
  token: str

  @classmethod
  def from_table(cls, table: TablePlan, spec: PrivacySpec) -> PanelInputs:
    space, excluded, space_error = _gower_space(table, spec)
    panel = table.panel
    names = [c.name for c in table.columns]
    r_rows: tuple[dict, ...] = ()
    h_rows: tuple[dict, ...] = ()
    if panel is not None:
      r_rows = tuple(_projected(row, names) for row in panel.r_rows)
      h_rows = tuple(_projected(row, names) for row in panel.h_rows)
    reason = space_error or _panel_reason(table, len(r_rows), len(h_rows))
    keys = _detection_keys(table)
    columns = tuple(
        DetectionPlan(
            name=c.name,
            kind=ColumnKind(c.kind),
            is_key=c.name in keys,
            grid=c.quantiles_src,
            dictionary=c.detection_dictionary)
        for c in table.columns
        if c.kind is not ColumnKind.NESTED)
    return cls(
        space=space,
        excluded=excluded,
        privacy_reason=reason,
        columns=columns,
        r_rows=r_rows,
        h_rows=h_rows,
        n_panel=0 if reason else len(r_rows),
        token=_token(spec, space, r_rows, h_rows))


def _projected(row: Mapping[str, Any], names: Sequence[str]) -> dict:
  """A panel row restricted to the plan's columns; a missing one stays
  missing (`build_nn_index` names it)."""
  return {name: row[name] for name in names if name in row}


def _gower_space(
    table: TablePlan,
    spec: PrivacySpec) -> tuple[GowerSpace | None, tuple[str, ...], str | None]:
  """The plan's Gower space over the non-key content (module docstring),
  the numeric columns left out for want of a source grid, and why there
  is no space (None when there is one)."""
  content = set(spec.layout.nonkey_columns)
  num_names: list[str] = []
  grids: list[tuple[float, ...]] = []
  cat_names: list[str] = []
  excluded: list[str] = []
  for column in table.columns:
    kind = ColumnKind(column.kind)
    if column.name not in content:
      continue
    if kind in _NUMERIC_KINDS:
      if column.quantiles_src is None:
        excluded.append(column.name)
        continue
      num_names.append(column.name)
      grids.append(column.quantiles_src)
    elif kind in _GOWER_CODED:
      cat_names.append(column.name)
  if not num_names and not cat_names:
    return None, tuple(excluded), _NO_FEATURES
  try:
    space = GowerSpace(
        num_grids=tuple(np.asarray(g, dtype=np.float64) for g in grids),
        num_names=tuple(num_names),
        cat_names=tuple(cat_names))
  except ValueError as exc:
    return None, tuple(excluded), _failure("the Gower space", exc)
  return space, tuple(excluded), None


def _panel_reason(table: TablePlan, n_r: int, n_h: int) -> str | None:
  """Why the panel cannot feed the nearest-neighbour metrics (None: it can)."""
  panel = table.panel
  if panel is None:
    return _NO_PANEL
  if not panel.verified:
    why = panel.reason or "no reason recorded"
    return f"{UNVERIFIED_REASON}: {why}"
  if n_r != n_h:
    return (f"|R| = {n_r} and |H| = {n_h} differ: the holdout test and its "
            "permutation variance need equal halves (D3)")
  if n_r < MIN_PANEL_ROWS:
    return (f"R and H hold {n_r} rows each, fewer than the "
            f"{MIN_PANEL_ROWS:,} the nearest-neighbour metrics need")
  return None


def _token(spec: PrivacySpec, space: GowerSpace | None,
           r_rows: Sequence[Mapping[str, Any]],
           h_rows: Sequence[Mapping[str, Any]]) -> str:
  """A digest of the side input's content: the feature set, the panel
  rows (in rank order) and the salt — the `Shared` tag of the index."""
  h = hashlib.blake2b(digest_size=16)
  for part in (spec.table, spec.salt, space.digest if space else "",
               str(len(r_rows)), str(len(h_rows))):
    h.update(part.encode())
    h.update(b"\x1f")
  for row in (*r_rows, *h_rows):
    h.update(json.dumps(row, sort_keys=True, default=str).encode())
  return h.hexdigest()


def _require_columns(rows: Sequence[Mapping[str, Any]], names: Sequence[str],
                     label: str) -> None:
  """Every row carries every plan column (the Gower encoder would read an
  absent one as NULL); the message names the columns, never a value."""
  wanted = frozenset(names)
  for row in rows:
    if not row.keys() >= wanted:
      missing = sorted(wanted - row.keys())
      raise ValueError(f"a {label} row has no {missing}: every panel row "
                       "must carry every plan column (a NULL is None, never "
                       "an absent key)")


# --------------------------------------------------------------------------
# sampling
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SamplePart:
  """One batch's contribution to a side's sample: its row count and the
  prefiltered rows as `(priority, row hash, payload)`."""
  rows: int
  entries: tuple[tuple[int, int, tuple[Any, ...]], ...] = ()


@dataclass(frozen=True)
class Sample:
  """A side's bottom-k sample: `(payload, multiplicity)` per key in
  priority order, and every row the side read."""
  entries: tuple[tuple[tuple[Any, ...], int], ...]
  rows: int

  def expanded(self, limit: int) -> tuple[list[tuple[Any, ...]], list[int]]:
    """The first `limit` rows of the sample with every key repeated by its
    multiplicity, and each row's key position."""
    rows: list[tuple[Any, ...]] = []
    keys: list[int] = []
    for position, (payload, count) in enumerate(self.entries):
      take = min(count, limit - len(rows))
      if take <= 0:
        break
      rows.extend([payload] * take)
      keys.extend([position] * take)
    return rows, keys


def _payloads(spec: PrivacySpec, batch: EncodedBatch,
              rows: np.ndarray) -> list[tuple[Any, ...]]:
  """The sampled rows' cells in `spec.sample_columns` order: floats (NULL
  None), booleans and the text block's strings (module docstring)."""
  positions = rows.tolist()
  columns: list[list[Any]] = []
  for block, j in spec.cells:
    if block == _TEXT:
      cells = batch.text[j]
      columns.append([cells[i] for i in positions])
      continue
    values = batch.num[rows, j].tolist()
    if block == _BOOL:
      columns.append([None if math.isnan(v) else bool(v) for v in values])
    else:
      columns.append([None if math.isnan(v) else v for v in values])
  if not columns:
    return [() for _ in positions]
  return list(zip(*columns, strict=True))


def sample_parts(spec: PrivacySpec,
                 batch: EncodedBatch) -> list[tuple[str, SamplePart]]:
  """A source or synthetic batch's `(side, SamplePart)`; other sides and
  other tables give nothing."""
  side = Side(batch.side).value
  if side not in _SAMPLED_SIDES or batch.table != spec.table:
    return []
  prio = sample_priority(batch.row_hash, spec.salt)
  limit = spec.limits[0 if side == _SOURCE else 1]
  rows = (
      np.arange(batch.n) if limit is None else np.flatnonzero(
          prio < np.uint64(limit)))
  payloads = _payloads(spec, batch, rows)
  entries = tuple((int(prio[i]), int(batch.row_hash[i]), payload)
                  for i, payload in zip(rows.tolist(), payloads, strict=True))
  return [(side, SamplePart(rows=batch.n, entries=entries))]


@dataclass(eq=False)
class _SampleAcc:
  bottom: BottomK
  rows: int = 0


class SampleCombineFn(beam.CombineFn):
  """`SamplePart`s → one `Sample` (BottomK(k) with exact multiplicities,
  and the side's row count). Order-free: BottomK's merge is exact."""

  def __init__(self, k: int):
    super().__init__()
    self._k = k

  def create_accumulator(self) -> _SampleAcc:
    return _SampleAcc(BottomK(self._k))

  def add_input(self, mutable_accumulator: _SampleAcc,
                element: SamplePart) -> _SampleAcc:
    mutable_accumulator.rows += element.rows
    for prio, key, payload in element.entries:
      mutable_accumulator.bottom.add(prio, key, payload)
    return mutable_accumulator

  def merge_accumulators(self,
                         accumulators: Iterable[_SampleAcc]) -> _SampleAcc:
    merged = self.create_accumulator()
    for acc in accumulators:
      merged = _SampleAcc(
          merged.bottom.merge(acc.bottom), merged.rows + acc.rows)
    return merged

  def extract_output(self, accumulator: _SampleAcc) -> Sample:
    return Sample(
        entries=tuple(accumulator.bottom.extract_with_counts()),
        rows=accumulator.rows)


# --------------------------------------------------------------------------
# the nearest-neighbour search
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class NNRecord:
  """One sampled synthetic key: its priority rank, the rows it stands for
  in the privacy sample (`weight`), its exact multiplicity (`count`), the
  sample rows before it (`offset`) and its cells."""
  rank: int
  weight: int
  count: int
  offset: int
  values: tuple[Any, ...]


def nn_records(spec: PrivacySpec, sample: Sample) -> list[NNRecord]:
  """The synthetic keys of the privacy sample, in priority order, weighted
  by multiplicity and cut at `spec.privacy_rows` rows (module docstring)."""
  out: list[NNRecord] = []
  offset = 0
  for rank, (payload, count) in enumerate(sample.entries):
    if offset >= spec.privacy_rows:
      break
    weight = min(count, spec.privacy_rows - offset)
    out.append(NNRecord(rank, weight, count, offset, payload))
    offset += weight
  return out


@dataclass(frozen=True, eq=False)
class NNIndex:
  """R and H encoded in the Gower space (float32 PIT / uint64 codes, the
  first `n_panel` rows each) and each R row's source code for the flag
  label (PK hash, else identity hash, else record hash)."""
  r_num: np.ndarray
  r_cat: np.ndarray
  h_num: np.ndarray
  h_cat: np.ndarray
  r_codes: np.ndarray


def build_nn_index(spec: PrivacySpec, inputs: PanelInputs) -> NNIndex:
  """The worker's R/H index (once per worker behind `Shared`).

  Raises:
    ValueError: no index can be built (`inputs.privacy_reason`), or a
      panel row lacks a plan column.
  """
  space = inputs.space
  if space is None or inputs.privacy_reason is not None:
    raise ValueError(inputs.privacy_reason or _NO_FEATURES)
  n = inputs.n_panel
  r_rows, h_rows = list(inputs.r_rows[:n]), list(inputs.h_rows[:n])
  _require_columns(r_rows, spec.layout.columns, "reference")
  _require_columns(h_rows, spec.layout.columns, "holdout")
  r_num, r_cat = space.encode(r_rows)
  h_num, h_cat = space.encode(h_rows)
  batch = BatchEncoder(
      spec.layout, Side.REFERENCE, salt=spec.salt).encode(r_rows)
  layout = spec.layout
  codes = (
      batch.pk_hash if layout.pk else
      batch.identity_hash if layout.identity else batch.nonkey_hash)
  return NNIndex(r_num, r_cat, h_num, h_cat, codes)


@dataclass(frozen=True, eq=False)
class NNPart:  # pylint: disable=too-many-instance-attributes  # one array per nearest-neighbour fact
  """The nearest-neighbour results of some synthetic keys, aligned: rank,
  weight, multiplicity, the two nearest R distances `d_r` (n, 2), the
  nearest R and H rows and H distance, and the flag handles; plus the
  Gower blocks of the keys whose rows open the sample (offset < |R|),
  which density reads at equal n."""
  rank: np.ndarray
  weight: np.ndarray
  count: np.ndarray
  d_r: np.ndarray
  i_r: np.ndarray
  d_h: np.ndarray
  i_h: np.ndarray
  handles: tuple[dict[str, Any] | None, ...]
  dense_rank: np.ndarray
  dense_num: np.ndarray
  dense_cat: np.ndarray


def gower_part(spec: PrivacySpec, inputs: PanelInputs, index: NNIndex,
               records: Sequence[NNRecord]) -> NNPart:
  """The exact Gower nearest neighbours of a batch of synthetic keys in R
  (two) and in H (one), chunked by `gower_knn`."""
  space = inputs.space
  if space is None:
    raise ValueError(inputs.privacy_reason or _NO_FEATURES)
  rows = [
      dict(zip(spec.sample_columns, r.values, strict=True)) for r in records
  ]
  q_num, q_cat = space.encode(rows)
  d_r, i_r = gower_knn(q_num, q_cat, index.r_num, index.r_cat, k=2)
  d_h, i_h = gower_knn(q_num, q_cat, index.h_num, index.h_cat, k=1)
  rank = np.array([r.rank for r in records], dtype=np.int64)
  dense = np.array([r.offset < inputs.n_panel for r in records], dtype=bool)
  return NNPart(
      rank=rank,
      weight=np.array([r.weight for r in records], dtype=np.int64),
      count=np.array([r.count for r in records], dtype=np.int64),
      d_r=d_r,
      i_r=i_r[:, 0],
      d_h=d_h[:, 0],
      i_h=i_h[:, 0],
      handles=tuple(_handle(spec, r.values) for r in records),
      dense_rank=rank[dense],
      dense_num=q_num[dense],
      dense_cat=q_cat[dense])


def merge_nn(parts: Iterable[NNPart]) -> NNPart | None:
  """The parts' union in priority order (None when there is none): the
  merge never depends on arrival order."""
  kept = [p for p in parts if p.rank.size]
  if not kept:
    return None
  if len(kept) == 1:
    return kept[0]

  def joined(name: str) -> np.ndarray:
    return np.concatenate([getattr(p, name) for p in kept])

  rank = joined("rank")
  order = np.argsort(rank, kind="stable")
  dense_rank = joined("dense_rank")
  dense_order = np.argsort(dense_rank, kind="stable")
  handles = [h for p in kept for h in p.handles]
  return NNPart(
      rank=rank[order],
      weight=joined("weight")[order],
      count=joined("count")[order],
      d_r=joined("d_r")[order],
      i_r=joined("i_r")[order],
      d_h=joined("d_h")[order],
      i_h=joined("i_h")[order],
      handles=tuple(handles[i] for i in order.tolist()),
      dense_rank=dense_rank[dense_order],
      dense_num=joined("dense_num")[dense_order],
      dense_cat=joined("dense_cat")[dense_order])


class NNMergeFn(beam.CombineFn):
  """`NNPart`s → one `NNPart` in priority order (None without input)."""

  def create_accumulator(self) -> list[NNPart]:
    return []

  def add_input(self, mutable_accumulator: list[NNPart],
                element: NNPart) -> list[NNPart]:
    mutable_accumulator.append(element)
    return mutable_accumulator

  def merge_accumulators(self,
                         accumulators: Iterable[list[NNPart]]) -> list[NNPart]:
    return [part for acc in accumulators for part in acc]

  def compact(self, accumulator: list[NNPart]) -> list[NNPart]:
    merged = merge_nn(accumulator)
    return [] if merged is None else [merged]

  def extract_output(self, accumulator: list[NNPart]) -> NNPart | None:
    return merge_nn(accumulator)


# --------------------------------------------------------------------------
# metric rows
# --------------------------------------------------------------------------
@dataclass
class _Block:
  """One block's rows (privacy or detection)."""
  metrics: list[MetricValue] = field(default_factory=list)
  profiles: list[ProfileValue] = field(default_factory=list)
  flags: list[RowFlag] = field(default_factory=list)


class _Emitter:
  """Collects one table's `MetricValue`s: every row a sample estimate."""

  def __init__(self, spec: PrivacySpec, sampling: Mapping[str, Any]):
    self.spec = spec
    self.sampling = dict(sampling)
    self.rows: list[MetricValue] = []

  def value(self, metric_id: str, value: float | None, *,
            detail: Mapping[str, Any], **fields_: Any) -> None:
    self.rows.append(
        MetricValue(
            metric_id=metric_id,
            table=self.spec.table,
            value=value,
            method=Method.SAMPLE,
            encoding_plan_digest=self.spec.encoding_plan_digest,
            detail={
                **detail,
                **self.sampling
            },
            **fields_))

  def skip(self, metric_id: str, reason: str, **fields_: Any) -> None:
    detail = dict(fields_.pop("detail", {}))
    self.value(metric_id, None, detail={"reason": reason, **detail}, **fields_)


def _skipped(spec: PrivacySpec, ids: Sequence[str],
             reason: str) -> list[MetricValue]:
  e = _Emitter(spec, _mode_detail(spec))
  for metric_id in ids:
    e.skip(metric_id, reason)
  return e.rows


def _failed_rows(table: str, digest: str, reason: str) -> list[MetricValue]:
  """Every owned id not_evaluated for a table that failed on the driver."""
  return [
      MetricValue(
          metric_id=metric_id,
          table=table,
          value=None,
          method=Method.SAMPLE,
          encoding_plan_digest=digest,
          detail={"reason": reason}) for metric_id in OWNED_METRIC_IDS
  ]


def _mode_detail(spec: PrivacySpec) -> dict[str, Any]:
  """The sampled-mode read rates (R72), when a side was read as a sample."""
  out: dict[str, Any] = {}
  if spec.rate_synthetic < 1.0:
    out["sample_rate"] = spec.rate_synthetic
  if spec.rate_source < 1.0:
    out["sample_rate_source"] = spec.rate_source
  return out


def _share_seen(spec: PrivacySpec, used: int, rows: int) -> float:
  """The share of the synthetic TABLE a metric saw: its sample rows over
  the rows read, times the sampled-mode read rate."""
  return min(1.0, used / rows) * spec.rate_synthetic if rows else 0.0


def _empty_sample(side: str, rows: int) -> str:
  """Why a side's sample is empty: no row read, or — the plan's row count
  far above the rows actually read — no row under the prefilter rate."""
  if not rows:
    return _NO_SOURCE if side == _SOURCE else _NO_SYNTHETIC
  return (f"no {side} row passed the sampling prefilter: the plan counted "
          f"far more rows than the {rows} read (a stale plan)")


def _edges_digest(edges: Sequence[float]) -> str:
  payload = np.asarray(edges, dtype="<f8").tobytes()
  return hashlib.blake2b(payload, digest_size=16).hexdigest()


# --------------------------------------------------------------------------
# the privacy block
# --------------------------------------------------------------------------
def privacy_block(spec: PrivacySpec, inputs: PanelInputs,
                  index_fn: Callable[[], NNIndex], merged: NNPart | None,
                  rows_seen: int, label_key: bytes,
                  failure: str | None) -> _Block:
  """The five row metrics, the DCR/NNDR histograms and the nearest-record
  flags of one table, or not_evaluated rows with the reason (a data error
  here too: `TABLE_ERRORS`).

  Raises:
    ValueError: an empty `label_key` (a wiring error, never a data one).
  """
  label_key = _checked_key(label_key)
  reason = failure or inputs.privacy_reason
  if reason is None and merged is None:
    reason = _empty_sample(_SYNTHETIC, rows_seen)
  if reason is not None or merged is None:
    return _Block(_skipped(spec, PRIVACY_METRIC_IDS, reason or _NO_SYNTHETIC))
  try:
    return _privacy_rows(spec, inputs, index_fn(), merged, rows_seen, label_key)
  except TABLE_ERRORS as exc:
    return _Block(_skipped(spec, PRIVACY_METRIC_IDS, _failure("privacy", exc)))


def _privacy_rows(spec: PrivacySpec, inputs: PanelInputs, index: NNIndex,
                  merged: NNPart, rows_seen: int, label_key: bytes) -> _Block:
  space = inputs.space
  assert space is not None  # privacy_reason is None
  n = inputs.n_panel
  w = merged.weight
  n_syn = int(w.sum())
  h_r, _ = gower_knn(index.h_num, index.h_cat, index.r_num, index.r_cat, k=2)
  d_r1 = merged.d_r[:, 0]
  closer, nn_mass = holdout_mass(
      np.repeat(d_r1, w),
      np.repeat(merged.i_r, w),
      np.repeat(merged.d_h, w),
      np.repeat(merged.i_h, w),
      n_r=n,
      n_h=n)
  n_eq = min(n, n_syn)
  density = coverage = base_density = base_coverage = None
  if n_eq > DENSITY_K:
    # the keys opening the sample are a prefix of the priority order
    dense_w = w[:merged.dense_rank.size]
    if not np.array_equal(merged.dense_rank,
                          merged.rank[:merged.dense_rank.size]):
      raise ValueError("the density sample is not a prefix of the privacy "
                       "sample (inconsistent nearest-neighbour parts)")
    fake_num = np.repeat(merged.dense_num, dense_w, axis=0)[:n_eq]
    fake_cat = np.repeat(merged.dense_cat, dense_w, axis=0)[:n_eq]
    density, coverage = density_coverage(
        index.r_num[:n_eq], index.r_cat[:n_eq], fake_num, fake_cat, k=DENSITY_K)
    base_density, base_coverage = density_coverage(
        index.h_num[:n_eq],
        index.h_cat[:n_eq],
        index.r_num[:n_eq],
        index.r_cat[:n_eq],
        k=DENSITY_K)
  summary = summarize_nn(
      NNPrivacyResult(
          dcr_syn_r=np.repeat(d_r1, w),
          dcr_syn_h=np.repeat(merged.d_h, w),
          dcr_h_r=h_r[:, 0],
          nndr_syn=np.repeat(nndr(merged.d_r), w),
          nndr_h=nndr(h_r),
          nn_mass=nn_mass,
          closer_to_r=float(nn_mass[:n].sum()) / n_syn,
          n_syn=n_syn,
          density=density,
          coverage=coverage))
  sampling = {
      **_mode_detail(spec),
      "sample_rows": n_syn,
      "sample_records": int(merged.rank.size),
  }
  common = {
      "sample_rate": _share_seen(spec, n_syn, rows_seen),
      "feature_set_digest": space.digest,
  }
  e = _Emitter(spec, sampling)
  e.value(
      "row.dcr_train_holdout_share",
      summary["dcr_train_holdout_share"],
      ci_low=summary["dcr_train_holdout_share_ci_low"],
      ci_high=summary["dcr_train_holdout_share_ci_high"],
      n_source=n,
      n_synthetic=n_syn,
      detail={
          "closer_to_reference": closer,
          "se_wilson": summary["dcr_train_holdout_share_se_wilson"],
          "se_perm": summary["dcr_train_holdout_share_se_perm"],
          "excluded": list(inputs.excluded),
      },
      **common)
  # (metric, synthetic distances, holdout distances, why a 0 holdout p5)
  for metric_id, syn, hold, zero_reason in (
      ("row.dcr_p5_ratio", "dcr_syn_r", "dcr_h_r", HOLDOUT_P5_ZERO),
      ("row.nndr_p5_ratio", "nndr_syn", "nndr_h", HOLDOUT_NNDR_P5_ZERO),
  ):
    fields_ = {
        "source_value": summary[f"{hold}_p5"],
        "synthetic_value": summary[f"{syn}_p5"],
        "n_source": n,
        "n_synthetic": n_syn,
        **common,
    }
    detail = {
        "synthetic_p50": summary[f"{syn}_p50"],
        "holdout_p50": summary[f"{hold}_p50"],
    }
    if summary[f"{hold}_p5"] == 0.0:
      e.skip(metric_id, zero_reason, detail=detail, **fields_)
    else:
      value = summary[metric_id.removeprefix("row.")]
      e.value(metric_id, value, detail=detail, **fields_)
  for metric_id, value, base in (("row.density", density, base_density),
                                 ("row.coverage", coverage, base_coverage)):
    fields_ = {"n_source": n_eq, "n_synthetic": n_eq, **common}
    if value is None:
      e.skip(metric_id, _FEW_AT_EQUAL_N.format(need=DENSITY_K + 1, n=n_eq),
             **fields_)
    else:
      e.value(
          metric_id,
          value,
          baseline_value=base,
          detail={"k": DENSITY_K},
          **fields_)
  profiles = [
      ProfileValue(
          table=spec.table,
          profile_kind=kind,
          side=side,
          payload=payload,
          n=int(payload["n"]),
          edges_digest=_edges_digest(payload["edges"]))
      for kind in ("dcr_hist", "nndr_hist")
      for side, payload in summary["profiles"][kind].items()
  ]
  return _Block(e.rows, profiles, _nearest_flags(spec, index, merged,
                                                 label_key))


def _nearest_flags(spec: PrivacySpec, index: NNIndex, merged: NNPart,
                   label_key: bytes) -> list[RowFlag]:
  """The `top_k` sampled synthetic keys nearest to a reference record."""
  d_r1 = merged.d_r[:, 0]
  ratios = nndr(merged.d_r)
  order = np.lexsort((merged.rank, d_r1))[:spec.top_k]
  flags = []
  for position, i in enumerate(order.tolist(), start=1):
    code = int(index.r_codes[merged.i_r[i]])
    distance = float(d_r1[i])
    flags.append(
        RowFlag(
            table=spec.table,
            check=NEAREST_RECORD,
            rank=position,
            synthetic_key=merged.handles[i],
            source_key_hash=(None if code == NULL_CODE else hashed_label(
                code, key=label_key)),
            source_set="R",
            distance=distance,
            score=1.0 - distance,
            detail={
                "holdout_distance": float(merged.d_h[i]),
                "nndr": float(ratios[i]),
                "multiplicity": int(merged.count[i]),
            }))
  return flags


# --------------------------------------------------------------------------
# the detection block
# --------------------------------------------------------------------------
def _head_masks(name: str, rows: Sequence[Mapping[str,
                                                  Any]]) -> tuple[str, ...]:
  """The source sample's `shape_of` masks holding at least 2 % of the
  column's non-empty values, most frequent first (ties by mask)."""
  masks = Counter(
      shape_of(value)
      for value in (row.get(name) for row in rows)
      if isinstance(value, str) and value)
  total = sum(masks.values())
  ranked = sorted(masks.items(), key=lambda kv: (-kv[1], kv[0]))
  return tuple(mask for mask, count in ranked
               if count >= _HEAD_MASK_SHARE * total)[:_MAX_HEAD_MASKS]


def detection_columns(
    inputs: PanelInputs, source_rows: Sequence[Mapping[str, Any]]
) -> tuple[list[DetectionColumn], list[str]]:
  """The plan's feature columns as `DetectionColumn`s (keys left out; the
  head masks from `source_rows`), and the numeric columns left out for
  want of a source grid.

  Raises:
    ValueError: a coded column without its plan dictionary.
  """
  columns: list[DetectionColumn] = []
  excluded: list[str] = []
  for plan in inputs.columns:
    if plan.is_key:
      continue
    if plan.kind in _NUMERIC_KINDS:
      if plan.grid is None:
        excluded.append(plan.name)
        continue
      columns.append(
          DetectionColumn(
              plan.name,
              plan.kind,
              False,
              grid=np.asarray(plan.grid, dtype=np.float64)))
    elif plan.kind in _CODED_KINDS:
      columns.append(
          DetectionColumn(
              plan.name, plan.kind, False, dictionary=plan.dictionary))
    else:
      columns.append(
          DetectionColumn(
              plan.name,
              plan.kind,
              False,
              head_masks=_head_masks(plan.name, source_rows)))
  return columns, excluded


def _feature_digest(columns: Sequence[DetectionColumn]) -> str:
  """blake2b-128 of the detection feature set: names, kinds, grids,
  dictionaries and head masks."""
  h = hashlib.blake2b(b"sdfb-detection/1", digest_size=16)
  for column in columns:
    h.update(json.dumps([column.name, str(column.kind)]).encode())
    if column.grid is not None:
      h.update(np.asarray(column.grid, dtype="<f8").tobytes())
    h.update(
        json.dumps(
            [list(column.dictionary or ()),
             list(column.head_masks or ())]).encode())
  return h.hexdigest()


def _as_rows(spec: PrivacySpec,
             payloads: Sequence[tuple[Any, ...]]) -> list[dict[str, Any]]:
  return [dict(zip(spec.sample_columns, p, strict=True)) for p in payloads]


def detection_block(spec: PrivacySpec, inputs: PanelInputs,
                    samples: Mapping[str,
                                     Sample], failure: str | None) -> _Block:
  """`table.detection_auc` and `table.pmse_ratio` of one table (with their
  R baselines), the ROC profile and the detectable flags — or
  not_evaluated rows with the reason."""
  source = samples.get(_SOURCE)
  synthetic = samples.get(_SYNTHETIC)
  reason = failure
  for side, sample in ((_SOURCE, source), (_SYNTHETIC, synthetic)):
    if reason is None and (sample is None or not sample.entries):
      reason = _empty_sample(side, sample.rows if sample else 0)
  if reason is not None or source is None or synthetic is None:
    return _Block(_skipped(spec, DETECTION_METRIC_IDS, reason or _NO_SYNTHETIC))
  try:
    return _detection_rows(spec, inputs, source, synthetic)
  except TABLE_ERRORS as exc:
    return _Block(
        _skipped(spec, DETECTION_METRIC_IDS, _failure("detection", exc)))


def _baseline(inputs: PanelInputs, src_rows: Sequence[Mapping[str, Any]],
              columns: Sequence[DetectionColumn],
              seed: int) -> tuple[dict[str, Any], str | None]:
  """The D4 baseline: the same test with R in the synthetic class."""
  if not inputs.r_rows:
    return {}, _NO_PANEL
  r_rows = list(inputs.r_rows)
  try:
    _require_columns(r_rows, [c.name for c in columns], "reference")
    x, y, cat_idx = featurize(src_rows, r_rows, columns)
    auc, _, _, _ = c2st_auc(x, y, cat_idx, folds=DETECTION_FOLDS, seed=seed)
    _, ratio, _, _ = pmse_ratio(x, y, cat_idx, seed=seed)
  except ValueError as exc:
    return {}, _failure("the baseline", exc)
  return {"auc": auc, "pmse_ratio": ratio, "n": int(y.size // 2)}, None


def _detection_rows(spec: PrivacySpec, inputs: PanelInputs, source: Sample,
                    synthetic: Sample) -> _Block:
  src_payloads, _ = source.expanded(spec.detection_rows)
  syn_payloads, syn_keys = synthetic.expanded(spec.detection_rows)
  n = min(len(src_payloads), len(syn_payloads))
  src_rows, syn_rows = _as_rows(spec,
                                src_payloads), _as_rows(spec, syn_payloads)
  columns, excluded = detection_columns(inputs, src_rows[:n])
  x, y, cat_idx = featurize(src_rows, syn_rows, columns)
  base, base_reason = _baseline(inputs, src_rows[:n], columns, spec.seed)
  sampling = {**_mode_detail(spec), "sample_rows": n}
  common = {
      "n_source": n,
      "n_synthetic": n,
      "sample_rate": _share_seen(spec, n, synthetic.rows),
      "feature_set_digest": _feature_digest(columns),
  }
  base_detail = ({
      "baseline_n": base["n"]
  } if base else {
      "baseline_reason": base_reason
  })
  e = _Emitter(spec, sampling)
  block = _Block()
  try:
    auc, lo, hi, oof = c2st_auc(
        x, y, cat_idx, folds=DETECTION_FOLDS, seed=spec.seed)
  except ValueError as exc:
    e.skip("table.detection_auc", _failure("detection", exc), **common)
  else:
    e.value(
        "table.detection_auc",
        auc,
        ci_low=lo,
        ci_high=hi,
        baseline_value=base.get("auc"),
        detail={
            "folds": DETECTION_FOLDS,
            "features": int(x.shape[1]),
            "columns": [c.name for c in columns],
            "excluded": excluded,
            **base_detail,
        },
        **common)
    block.profiles.append(
        ProfileValue(
            table=spec.table,
            profile_kind="roc_curve",
            side="both",
            payload={
                "points": roc_points(y, oof, max_points=_ROC_POINTS),
                "auc": auc,
                "ci_low": lo,
                "ci_high": hi,
                "n_source": n,
                "n_synthetic": n,
            },
            n=2 * n))
    if lo > _CHANCE:
      block.flags = _detectable_flags(spec, synthetic, syn_keys, oof[n:])
  try:
    pmse, ratio, k, ceiling = pmse_ratio(x, y, cat_idx, seed=spec.seed)
  except ValueError as exc:
    e.skip("table.pmse_ratio", _failure("pmse", exc), **common)
  else:
    e.value(
        "table.pmse_ratio",
        ratio,
        baseline_value=base.get("pmse_ratio"),
        detail={
            "pmse": pmse,
            "k": k,
            "ceiling": ceiling,
            **base_detail,
        },
        **common)
  block.metrics = e.rows
  return block


def _detectable_flags(spec: PrivacySpec, synthetic: Sample, keys: Sequence[int],
                      scores: np.ndarray) -> list[RowFlag]:
  """The `top_k` sampled synthetic keys the classifier finds most surely
  synthetic (out of fold), one flag per key."""
  order = np.lexsort((np.arange(scores.size), -scores))
  flags: list[RowFlag] = []
  seen: set[int] = set()
  for j in order.tolist():
    key = keys[j]
    if key in seen:
      continue
    seen.add(key)
    payload, count = synthetic.entries[key]
    probability = float(scores[j])
    flags.append(
        RowFlag(
            table=spec.table,
            check=DETECTABLE,
            rank=len(flags) + 1,
            synthetic_key=_handle(spec, payload),
            source_key_hash=None,
            source_set=None,
            distance=None,
            score=probability,
            detail={
                "p_synthetic": probability,
                "multiplicity": count
            }))
    if len(flags) == spec.top_k:
      break
  return flags


# --------------------------------------------------------------------------
# in process (the reference implementation the Beam path is held to)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PrivacyResult:
  metrics: list[MetricValue]
  profiles: list[ProfileValue]
  flags: list[RowFlag]


def _ordered(metrics: Iterable[MetricValue]) -> list[MetricValue]:
  order = {metric_id: i for i, metric_id in enumerate(OWNED_METRIC_IDS)}
  return sorted(metrics, key=lambda mv: order[mv.metric_id])


def privacy_outputs(table: TablePlan,
                    batches: Iterable[EncodedBatch],
                    *,
                    salt: str,
                    label_key: bytes,
                    privacy_sample_rows: int = 50_000,
                    detection_sample_rows: int = 50_000,
                    row_flags_top_k: int = 100) -> PrivacyResult:
  """Both blocks of one table in one process, exactly as the Beam path
  computes them. A data error (`TABLE_ERRORS`) makes the block it hits
  not_evaluated, as in Beam.

  Raises:
    ValueError: an empty `label_key`.
  """
  label_key = _checked_key(label_key)
  try:
    spec = PrivacySpec.from_table(
        table,
        salt=salt,
        privacy_sample_rows=privacy_sample_rows,
        detection_sample_rows=detection_sample_rows,
        row_flags_top_k=row_flags_top_k)
    inputs = PanelInputs.from_table(table, spec)
  except TABLE_ERRORS as exc:
    return PrivacyResult(
        _failed_rows(table.name, table.encoding_plan_digest,
                     _failure("privacy", exc)), [], [])
  combine = SampleCombineFn(spec.sample_k)
  accs = {side: combine.create_accumulator() for side in _SAMPLED_SIDES}
  failure: str | None = None
  try:
    for batch in batches:
      for side, part in sample_parts(spec, batch):
        accs[side] = combine.add_input(accs[side], part)
  except TABLE_ERRORS as exc:
    failure = _failure("privacy", exc)
  samples = {side: combine.extract_output(acc) for side, acc in accs.items()}
  detection = detection_block(spec, inputs, samples, failure)
  cache: list[NNIndex] = []

  def index_fn() -> NNIndex:
    if not cache:
      cache.append(build_nn_index(spec, inputs))
    return cache[0]

  merged: NNPart | None = None
  nn_failure = failure
  if nn_failure is None and inputs.privacy_reason is None:
    try:
      records = nn_records(spec, samples[_SYNTHETIC])
      merged = merge_nn(
          gower_part(spec, inputs, index_fn(), records[i:i + NN_BATCH_ROWS])
          for i in range(0, len(records), NN_BATCH_ROWS))
    except TABLE_ERRORS as exc:
      nn_failure = _failure("privacy", exc)
  nearest = privacy_block(spec, inputs, index_fn, merged,
                          samples[_SYNTHETIC].rows, label_key, nn_failure)
  return PrivacyResult(
      _ordered([*nearest.metrics,
                *detection.metrics]), [*nearest.profiles, *detection.profiles],
      [*nearest.flags, *detection.flags])


# --------------------------------------------------------------------------
# Beam
# --------------------------------------------------------------------------
def _first_reason(failures: Iterable[tuple[str, str]],
                  scopes: frozenset[str]) -> str | None:
  reasons = sorted(reason for scope, reason in failures if scope in scopes)
  return reasons[0] if reasons else None


class _SamplePartsFn(beam.DoFn):
  """`EncodedBatch` → `(side, SamplePart)`; tagged table-scope failures."""

  def __init__(self, spec: PrivacySpec):
    super().__init__()
    self._spec = spec

  def process(self, element: EncodedBatch) -> Iterator[Any]:
    try:
      parts = sample_parts(self._spec, element)
    except TABLE_ERRORS as exc:
      yield beam.pvalue.TaggedOutput(_FAILED,
                                     (_TABLE, _failure("privacy", exc)))
      return
    yield from parts


def _records_of(item: tuple[str, Sample], spec: PrivacySpec,
                run: bool) -> list[NNRecord]:
  side, sample = item
  if not run or side != _SYNTHETIC:
    return []
  return nn_records(spec, sample)


class GowerNNFn(beam.DoFn):
  """A batch of `NNRecord`s → one `NNPart`; tagged privacy-scope failures.

  The R/H index is built once per worker from the `PanelInputs` side
  input, behind `Shared` (tagged by the input's token), on the first
  `process()` call — a side input cannot be read in `setup()` — and is
  kept alive by the DoFn.
  """

  def __init__(self, spec: PrivacySpec, shared: Shared):
    super().__init__()
    self._spec = spec
    self._shared = shared
    self._index: NNIndex | None = None

  def process(self, element: Sequence[NNRecord],
              inputs: PanelInputs) -> Iterator[Any]:
    spec = self._spec
    try:
      self._index = self._shared.acquire(
          functools.partial(build_nn_index, spec, inputs), tag=inputs.token)
      part = gower_part(spec, inputs, self._index, element)
    except TABLE_ERRORS as exc:
      yield beam.pvalue.TaggedOutput(_FAILED,
                                     (_PRIVACY, _failure("privacy", exc)))
      return
    yield part


class _PrivacyEmitFn(beam.DoFn):
  """The merged `NNPart` (or None) → the privacy block's rows; the index
  comes from the same `Shared` handle as `GowerNNFn`'s."""

  def __init__(self, spec: PrivacySpec, shared: Shared):
    super().__init__()
    self._spec = spec
    self._shared = shared
    self._index: NNIndex | None = None

  def process(self, element: NNPart | None, inputs: PanelInputs,
              label_key: bytes, rows: Mapping[str, int],
              failures: Sequence[tuple[str, str]]) -> Iterator[Any]:
    spec = self._spec

    def index_fn() -> NNIndex:
      self._index = self._shared.acquire(
          functools.partial(build_nn_index, spec, inputs), tag=inputs.token)
      return self._index

    block = privacy_block(
        spec, inputs, index_fn, element, rows.get(_SYNTHETIC, 0), label_key,
        _first_reason(failures, frozenset({_TABLE, _PRIVACY})))
    yield from _tagged(block)


class _DetectionFn(beam.DoFn):
  """Both samples of a table (one element) → the detection block's rows."""

  def __init__(self, spec: PrivacySpec):
    super().__init__()
    self._spec = spec

  def process(self, element: Mapping[str, Sample], inputs: PanelInputs,
              failures: Sequence[tuple[str, str]]) -> Iterator[Any]:
    block = detection_block(self._spec, inputs, element,
                            _first_reason(failures, frozenset({_TABLE})))
    yield from _tagged(block)


def _tagged(block: _Block) -> Iterator[Any]:
  yield from block.metrics
  for profile in block.profiles:
    yield beam.pvalue.TaggedOutput(_PROFILES, profile)
  for flag in block.flags:
    yield beam.pvalue.TaggedOutput(_FLAGS, flag)


def _rows_of(item: tuple[str, Sample]) -> tuple[str, int]:
  side, sample = item
  return side, sample.rows


class _ByTable(beam.PartitionFn):
  """A batch's partition: its table's index in `names`; any other table
  (failed on the driver, or not evaluated here) goes to the last one,
  which nothing reads."""

  def __init__(self, names: Sequence[str]):
    super().__init__()
    self._names = tuple(names)

  def partition_for(self, element: Any, num_partitions: int, *args: Any,
                    **kwargs: Any) -> int:
    del args, kwargs  # no extra partition arguments
    if element.table in self._names:
      return self._names.index(element.table)
    return num_partitions - 1


class Privacy(beam.PTransform):
  """`PCollection[EncodedBatch]` (every table, every side) → `{"metrics":
  PCollection[MetricValue], "profiles": PCollection[ProfileValue],
  "flags": PCollection[RowFlag]}` (module docstring).

  `label_key` is the one-element key PCollection `beam.label_key.LabelKey`
  makes on a worker, read as a side input when the nearest-record flags
  are labelled, so the key never enters the job graph (Rulings R64, R68).
  `salt` must be the salt the batches were encoded with (the plan's). The
  sample sizes and the flag cap are the plan's knobs
  (`privacy_sample_rows`, `detection_sample_rows`, `row_flags_top_k`).
  Each table's `PanelInputs` (Gower space, detection columns, panel rows)
  is built here, on the driver, and shipped as one side input; only its
  slim `PrivacySpec` is pickled into the DoFns. A table whose spec cannot
  be built (`TABLE_ERRORS`) is not evaluated, with the reason; the others
  run.

  Raises:
    TypeError: `label_key` is not a PCollection (bytes here would be
      pickled into the graph).
  """

  def __init__(self,
               tables: Sequence[TablePlan],
               *,
               salt: str,
               label_key: beam.PCollection,
               privacy_sample_rows: int = 50_000,
               detection_sample_rows: int = 50_000,
               row_flags_top_k: int = 100):
    super().__init__()
    if not isinstance(label_key, beam.PCollection):
      raise TypeError("label_key must be the LabelKey PCollection, never "
                      "bytes: a constructor argument is pickled into the job "
                      "graph (Ruling R68)")
    self._label_key = label_key
    self._all = tuple(table.name for table in tables)
    self._specs: dict[str, PrivacySpec] = {}
    self._inputs: dict[str, PanelInputs] = {}
    self._failed: dict[str, list[MetricValue]] = {}
    for table in tables:
      try:
        spec = PrivacySpec.from_table(
            table,
            salt=salt,
            privacy_sample_rows=privacy_sample_rows,
            detection_sample_rows=detection_sample_rows,
            row_flags_top_k=row_flags_top_k)
        inputs = PanelInputs.from_table(table, spec)
      except TABLE_ERRORS as exc:
        self._failed[table.name] = _failed_rows(table.name,
                                                table.encoding_plan_digest,
                                                _failure("privacy", exc))
        continue
      self._specs[table.name] = spec
      self._inputs[table.name] = inputs

  def expand(self, input_or_inputs: beam.PCollection) -> dict[str, Any]:
    batches = input_or_inputs
    p = batches.pipeline
    names = tuple(name for name in self._all if name in self._specs)
    parts = batches | "ByTable" >> beam.Partition(
        _ByTable(names),
        len(names) + 1)
    outputs: dict[str, list[beam.PCollection]] = {
        _METRICS: [],
        _PROFILES: [],
        _FLAGS: []
    }
    for i, name in enumerate(names):
      for tag, pcoll in self._table(p, parts[i], name).items():
        outputs[tag].append(pcoll)
    failed = [mv for name in self._all for mv in self._failed.get(name, [])]
    outputs[_METRICS].append(p | "FailedTables" >> beam.Create(failed))
    for tag in (_PROFILES, _FLAGS):
      if not outputs[tag]:
        outputs[tag].append(p | f"No{tag.title()}" >> beam.Create([]))
    return {
        tag: pcolls | f"Flatten{tag.title()}" >> beam.Flatten()
        for tag, pcolls in outputs.items()
    }

  def _table(self, p: beam.Pipeline, batches: beam.PCollection,
             name: str) -> dict[str, beam.PCollection]:
    spec, inputs = self._specs[name], self._inputs[name]
    side_input = beam.pvalue.AsSingleton(
        p | f"Inputs[{name}]" >> beam.Create([inputs]))
    sampled = batches | f"SampleParts[{name}]" >> beam.ParDo(
        _SamplePartsFn(spec)).with_outputs(
            _FAILED, main=_PARTS)
    seeds = p | f"SampleSeeds[{name}]" >> beam.Create(
        [(side, SamplePart(rows=0)) for side in _SAMPLED_SIDES])
    samples = (
        (sampled[_PARTS], seeds)
        | f"FlattenParts[{name}]" >> beam.Flatten()
        |
        f"Sample[{name}]" >> beam.CombinePerKey(SampleCombineFn(spec.sample_k)))
    detection = (
        samples
        | f"Samples[{name}]" >> beam.combiners.ToDict()
        | f"Detection[{name}]" >> beam.ParDo(
            _DetectionFn(spec), side_input, beam.pvalue.AsList(
                sampled[_FAILED])).with_outputs(
                    _PROFILES, _FLAGS, main=_METRICS))
    shared = Shared()
    searched = (
        samples
        | f"Records[{name}]" >> beam.FlatMap(_records_of, spec,
                                             inputs.privacy_reason is None)
        | f"Spread[{name}]" >> beam.Reshuffle()
        | f"Batch[{name}]" >> beam.BatchElements(
            min_batch_size=1, max_batch_size=NN_BATCH_ROWS)
        | f"GowerNN[{name}]" >> beam.ParDo(GowerNNFn(
            spec, shared), side_input).with_outputs(_FAILED, main=_PARTS))
    rows = samples | f"Rows[{name}]" >> beam.Map(_rows_of)
    failures = ((sampled[_FAILED], searched[_FAILED])
                | f"FlattenFailures[{name}]" >> beam.Flatten())
    nearest = (
        searched[_PARTS]
        | f"NNMerge[{name}]" >> beam.CombineGlobally(NNMergeFn())
        | f"PrivacyEmit[{name}]" >> beam.ParDo(
            _PrivacyEmitFn(spec, shared), side_input,
            beam.pvalue.AsSingleton(self._label_key), beam.pvalue.AsDict(rows),
            beam.pvalue.AsList(failures)).with_outputs(
                _PROFILES, _FLAGS, main=_METRICS))
    merged_metrics = ((nearest[_METRICS], detection[_METRICS])
                      | f"Metrics[{name}]" >> beam.Flatten())
    merged_profiles = ((nearest[_PROFILES], detection[_PROFILES])
                       | f"Profiles[{name}]" >> beam.Flatten())
    merged_flags = ((nearest[_FLAGS], detection[_FLAGS])
                    | f"Flags[{name}]" >> beam.Flatten())
    return {
        _METRICS: merged_metrics,
        _PROFILES: merged_profiles,
        _FLAGS: merged_flags
    }
