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
"""Row-level privacy maths: exact Gower nearest neighbours, the holdout DCR
test, NNDR, and k-NN density/coverage, feeding `row.dcr_train_holdout_share`,
`row.dcr_p5_ratio`, `row.nndr_p5_ratio`, `row.density` and `row.coverage`
(`src/sdfb_evaluation/catalogue/metrics.yaml`).

The feature space (`GowerSpace`) comes from the evaluation plan, never from
the rows being compared, so R, H and every synthetic batch are encoded
identically on every worker:

  - numeric and temporal columns map through the SOURCE quantile grid's
    mid-CDF probability integral transform to `u` in `[0, 1]` (the Parzen
    mid-distribution convention of Rulings R16/R24: a value on a run of
    identical grid points maps to the middle of that run's probability
    range), so a numeric feature's Gower range normalisation is the source's
    own rank scale — robust to outliers, and tie-aware in the spirit of
    Podani's (1999) ordinal extension of Gower's coefficient;
  - categorical, boolean, text and identifier columns map to `hash64` codes;
    two cells match only on the identical canonical value, so distinct rare
    values stay distance 1 apart (no top-K / "other" collapse);
  - key columns (and nested ones) are left out of the space by the plan.

The Gower (1971) distance between two rows is the mean of per-feature
dissimilarities over all `d` features: `|u_q - u_r|` for a numeric feature,
`1[code_q != code_r]` for a categorical one, and for either kind a NULL
against a NULL is 0 and a NULL against a value is 1 (so every feature always
counts; the denominator is `d` for every pair).

`gower_knn` is exact brute force: it streams query chunks against the whole
reference set, accumulating per-feature dissimilarities into one float32
`(chunk, |R|)` plane, then selects the `k` smallest per row with
`np.partition`, ties broken by the lower reference index. No tree index is
used; Gower's mixed metric does not suit one and the reference sets are
small (10k rows). Query rows are independent, so the result for any row is
bit-identical whatever the chunk size or the other rows in its chunk — Beam
can split the synthetic sample across workers and concatenate.

The holdout DCR test follows Platzer & Reutterer (2021): R (the rows the
generator saw) and an equal-size, exchangeable holdout H drawn from the same
source; a synthetic row is "closer to R" when its nearest-R distance is
below its nearest-H distance, ties counting one half. A generator that does
not memorise sits at a share of 0.5 whatever the data's density. NNDR
(nearest over second-nearest distance to R) follows the singling-out
reading of Giomi et al. (2023). Density and coverage are Naeem et al.
(2020), k-NN balls around the real rows, at equal sample sizes.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import numpy as np

from sdfb_evaluation.canonical import hash_matrix, numeric_value
from sdfb_evaluation.stats.binned import bin_counts
from sdfb_evaluation.stats.noise import wilson_interval

# Bumped whenever the encoding changes meaning (grid convention, null codes,
# distance), so a cached encoding keyed by `GowerSpace.digest` cannot be
# reused across incompatible versions.
_DIGEST_VERSION = b"sdfb-gower/1 mid-cdf-pit hash64"

# Upper bound for one chunk's working set, in bytes (64 MB).
_MAX_WORKING_SET_BYTES = 64_000_000
# The chunk's real working set per (query, reference) pair: the float32
# accumulator, a float32 scratch plane, a byte mismatch counter, a boolean
# plane, the float32 partition copy and a boolean selection mask (15 bytes;
# up to 26 on a chunk whose every row is tied at its k-th value). The budget
# charges `4 * max(d, 8)` bytes per pair: the documented `chunk * |R| * d * 4`
# bound for d >= 8, and still above the real working set for smaller d.
_MIN_FEATURES_CHARGED = 8

_MIN_GRID_POINTS = 2
# Encoded blocks are (rows, features) matrices.
_BLOCK_NDIM = 2
_MIN_SET_ROWS = 2
# Nearest neighbours needed for NNDR: the first and the second.
_NNDR_K = 2

_P5 = 0.05
_P50 = 0.5
# Fixed profile bins over [0, 1] for DCR and NNDR histograms: 49 interior
# edges 0.02, ..., 0.98, counted like `binned.bin_counts` (the GUI's
# `distanceHistPayloadSchema`: `counts` has `len(edges) + 1` entries).
_HIST_BINS = 50
_HIST_EDGES = tuple(i / _HIST_BINS for i in range(1, _HIST_BINS))


def _validated_grid(name: str, grid: Any) -> np.ndarray:
  """`grid` as a read-only 1-D float64 array, or `ValueError` if unusable."""
  arr = np.array(grid, dtype=np.float64)
  if arr.ndim != 1 or arr.size < _MIN_GRID_POINTS:
    raise ValueError(
        f"the quantile grid of numeric column {name!r} needs at least 2 "
        f"points (a 1-D APPROX_QUANTILES grid), got shape {arr.shape}")
  if not np.isfinite(arr).all():
    raise ValueError(
        f"the quantile grid of numeric column {name!r} must be finite")
  if np.any(np.diff(arr) < 0):
    raise ValueError(
        f"the quantile grid of numeric column {name!r} must be non-decreasing")
  arr.setflags(write=False)
  return arr


def _parse_numeric_text(text: str) -> float | None:
  """A numeric string (`"10.5"`) or an ISO-8601 date/time as epoch seconds."""
  try:
    return float(text)
  except ValueError:
    pass
  try:
    parsed = datetime.fromisoformat(text)
  except ValueError:
    return None
  aware = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
  return aware.timestamp()


def _numeric_reading(column: str, value: Any) -> float:
  """One numeric/temporal cell as a float; `NaN` for NULL or non-finite.

  Beam reads give `Decimal`/`int`/`float`/`datetime`/`date` values, which
  `numeric_value` maps (temporal to epoch seconds, a naive `datetime` read as
  UTC); a canonicalised row carries the same values as strings (`"10.5"`, an
  ISO timestamp), which are parsed back. A value with no numeric reading at
  all is a plan/data mismatch and raises rather than being silently treated
  as NULL; the message names the column and the type, never the value.
  """
  if value is None:
    return math.nan
  reading = numeric_value(value)
  if reading is None and isinstance(value, str):
    reading = _parse_numeric_text(value)
  if reading is None:
    raise ValueError(f"numeric column {column!r} holds a "
                     f"{type(value).__name__} value with no numeric reading")
  return reading if math.isfinite(reading) else math.nan


def _mid_cdf_pit(x: np.ndarray, grid: np.ndarray) -> np.ndarray:
  """The source mid-CDF of every `x` through a quantile grid; `NaN` stays `NaN`.

  `grid[i]` is the quantile at probability `i / (len(grid) - 1)`. A value on
  a run of identical grid points `grid[a..b]` (an atom) maps to the middle of
  that run's probability range, `(p_a + p_b) / 2` — the Parzen mid-CDF
  `F - p/2` (Rulings R16/R24), so a constant source maps its value to 1/2.
  Between grid points the CDF is linear; below the grid it is 0, above 1.
  """
  probs = np.linspace(0.0, 1.0, grid.size)
  out = np.full(x.shape, np.nan)
  present = ~np.isnan(x)
  values = x[present]
  lo = np.searchsorted(grid, values, side="left")
  hi = np.searchsorted(grid, values, side="right")
  u = np.empty(values.shape)
  on_grid = hi > lo
  u[on_grid] = 0.5 * (probs[lo[on_grid]] + probs[hi[on_grid] - 1])
  u[~on_grid & (lo == 0)] = 0.0
  u[~on_grid & (lo == grid.size)] = 1.0
  inner = ~on_grid & (lo > 0) & (lo < grid.size)
  right = lo[inner]
  g0, g1 = grid[right - 1], grid[right]
  p0, p1 = probs[right - 1], probs[right]
  u[inner] = p0 + (values[inner] - g0) / (g1 - g0) * (p1 - p0)
  out[present] = np.clip(u, 0.0, 1.0)
  return out


@dataclass(frozen=True, eq=False)
class GowerSpace:
  """The plan's Gower feature space: numeric grids and categorical columns.

  `num_grids[j]` is the source's quantile grid for `num_names[j]` (a
  1,001-point `APPROX_QUANTILES(x, 1000)` grid, temporal columns in epoch
  seconds), evenly spaced in probability; `cat_names` are the categorical,
  boolean, text and identifier columns. Keys are never features. Equality
  is identity (`eq=False`: comparing arrays elementwise is not a truth
  value); compare spaces by `digest`.
  """
  num_grids: tuple[np.ndarray, ...]
  num_names: tuple[str, ...]
  cat_names: tuple[str, ...]
  _digest: str = field(init=False, repr=False, default="")

  def __post_init__(self) -> None:
    num_names = tuple(self.num_names)
    cat_names = tuple(self.cat_names)
    if len(self.num_grids) != len(num_names):
      raise ValueError(
          f"num_grids has {len(self.num_grids)} grids for {len(num_names)} "
          "numeric columns")
    names = num_names + cat_names
    if len(set(names)) != len(names):
      raise ValueError(f"duplicate column names in the Gower space: {names}")
    if not names:
      raise ValueError("the Gower space has no features")
    grids = tuple(
        _validated_grid(name, grid)
        for name, grid in zip(num_names, self.num_grids, strict=True))
    object.__setattr__(self, "num_grids", grids)
    object.__setattr__(self, "num_names", num_names)
    object.__setattr__(self, "cat_names", cat_names)
    object.__setattr__(self, "_digest", self._compute_digest())

  def _compute_digest(self) -> str:
    h = hashlib.blake2b(_DIGEST_VERSION, digest_size=16)

    def _put(data: bytes) -> None:
      h.update(len(data).to_bytes(8, "big"))
      h.update(data)

    for name, grid in zip(self.num_names, self.num_grids, strict=True):
      _put(b"numeric")
      _put(name.encode("utf-8"))
      _put(grid.astype("<f8").tobytes())
    for name in self.cat_names:
      _put(b"categorical")
      _put(name.encode("utf-8"))
    return h.hexdigest()

  @property
  def n_features(self) -> int:
    """`d`, the Gower denominator: numeric plus categorical features."""
    return len(self.num_names) + len(self.cat_names)

  @property
  def digest(self) -> str:
    """The `feature_set_digest`: blake2b-128 hex of names, kinds and grids."""
    return self._digest

  def encode(
      self, rows: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    """`(num, cat)`: float32 `(n, dn)` PIT values (`NaN` = NULL) and uint64
    `(n, dc)` `hash64` codes (`NULL_CODE` = NULL).

    A column absent from a row reads as NULL. Raises `ValueError` for a
    numeric/temporal value with no numeric reading (see `_numeric_reading`).
    """
    n = len(rows)
    num = np.empty((n, len(self.num_names)), dtype=np.float32)
    for j, (name, grid) in enumerate(
        zip(self.num_names, self.num_grids, strict=True)):
      x = np.fromiter((_numeric_reading(name, row.get(name)) for row in rows),
                      dtype=np.float64,
                      count=n)
      num[:, j] = _mid_cdf_pit(x, grid)
    return num, hash_matrix(rows, self.cat_names)


def effective_chunk(n_ref: int, n_features: int, chunk: int = 64) -> int:
  """The query rows per chunk: at most `chunk`, and within the 64 MB budget.

  The working set is charged `4 * max(d, 8)` bytes per (query, reference)
  pair, so `effective_chunk * n_ref * d * 4 <= 64 MB` whenever one query row
  fits (at `n_ref = 10_000`, `d = 50`: 32 rows, 64 MB).
  """
  per_row = max(1, n_ref) * 4 * max(n_features, _MIN_FEATURES_CHARGED)
  return max(1, min(chunk, _MAX_WORKING_SET_BYTES // per_row))


def _as_blocks(num: Any, cat: Any, label: str) -> tuple[np.ndarray, np.ndarray]:
  """Validated float32 `(n, dn)` and uint64 `(n, dc)` blocks for one side."""
  num_arr = np.asarray(num, dtype=np.float32)
  cat_arr = np.asarray(cat)
  if num_arr.ndim != _BLOCK_NDIM or cat_arr.ndim != _BLOCK_NDIM:
    raise ValueError(f"{label} blocks must be 2-D, got {num_arr.shape} and "
                     f"{cat_arr.shape}")
  if cat_arr.size and cat_arr.dtype.kind not in "ui":
    raise ValueError(f"{label} categorical codes must be integers, got "
                     f"{cat_arr.dtype}")
  cat_arr = cat_arr.astype(np.uint64, copy=False)
  if num_arr.shape[0] != cat_arr.shape[0]:
    raise ValueError(f"{label} numeric and categorical blocks disagree on "
                     f"rows: {num_arr.shape[0]} vs {cat_arr.shape[0]}")
  finite = num_arr[~np.isnan(num_arr)]
  if finite.size and (finite.min() < 0.0 or finite.max() > 1.0):
    raise ValueError(f"{label} numeric features must lie in [0, 1] (PIT "
                     "values) or be NaN for NULL")
  return num_arr, cat_arr


class _Reference:
  """The reference side, laid out per feature for the chunk loop."""

  def __init__(self, num: np.ndarray, cat: np.ndarray) -> None:
    self.num = num
    self.cat = cat
    self.n = num.shape[0]
    self.num_cols = [
        np.ascontiguousarray(num[:, j]) for j in range(num.shape[1])
    ]
    self.num_null_idx = [np.flatnonzero(np.isnan(col)) for col in self.num_cols]
    # Per feature: what a NULL query cell scores against each reference cell
    # (1 against a value, 0 against a NULL).
    self.num_present = [
        (~np.isnan(col)).astype(np.float32) for col in self.num_cols
    ]
    self.cat_cols = [
        np.ascontiguousarray(cat[:, j]) for j in range(cat.shape[1])
    ]
    self.n_features = num.shape[1] + cat.shape[1]


def _pair_sums(q_num: np.ndarray, q_cat: np.ndarray, ref: _Reference,
               acc: np.ndarray, scratch: np.ndarray, mismatch: np.ndarray,
               neq: np.ndarray) -> np.ndarray:
  """The `(c, |R|)` float32 Gower SUMS (not yet divided by `d`) into `acc`.

  Numeric features are accumulated first, in feature order, then the
  integer mismatch count of the categorical features is added once, so a
  pair's sum depends only on its own two rows: identical whatever the chunk.
  A NULL cell is patched after the subtraction (its `NaN` differences are
  overwritten by exactly 0 or 1), so non-NULL pairs see the plain `|u - v|`.
  """
  acc.fill(0.0)
  for j, rcol in enumerate(ref.num_cols):
    qcol = q_num[:, j]
    np.subtract(qcol[:, np.newaxis], rcol[np.newaxis, :], out=scratch)
    np.abs(scratch, out=scratch)
    q_null = np.isnan(qcol)
    if q_null.any():
      scratch[q_null, :] = ref.num_present[j]
    r_null = ref.num_null_idx[j]
    if r_null.size:
      scratch[:, r_null] = (~q_null).astype(np.float32)[:, np.newaxis]
    np.add(acc, scratch, out=acc)
  if ref.cat_cols:
    mismatch.fill(0)
    for j, rcol in enumerate(ref.cat_cols):
      np.not_equal(q_cat[:, j, np.newaxis], rcol[np.newaxis, :], out=neq)
      np.add(mismatch, neq, out=mismatch)
    np.add(acc, mismatch, out=acc)
  return acc


def _iter_pair_sums(q_num: np.ndarray, q_cat: np.ndarray, ref: _Reference,
                    chunk: int) -> Iterator[tuple[int, np.ndarray]]:
  """`(start, sums)` per query chunk; `sums` is reused by the next chunk."""
  step = max(1,
             min(effective_chunk(ref.n, ref.n_features, chunk), q_num.shape[0]))
  n_cat = len(ref.cat_cols)
  count_dtype = np.uint8 if n_cat <= np.iinfo(np.uint8).max else np.uint32
  acc = np.empty((step, ref.n), dtype=np.float32)
  scratch = np.empty((step, ref.n), dtype=np.float32)
  mismatch = np.empty((step, ref.n), dtype=count_dtype)
  neq = np.empty((step, ref.n), dtype=bool)
  for start in range(0, q_num.shape[0], step):
    stop = min(start + step, q_num.shape[0])
    c = stop - start
    yield start, _pair_sums(q_num[start:stop], q_cat[start:stop], ref, acc[:c],
                            scratch[:c], mismatch[:c], neq[:c])


def _k_smallest(sums: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
  """The `k` smallest per row, ascending, ties broken by the lower index.

  `np.partition` finds each row's k-th smallest value `t`; the rows with
  exactly `k` values `<= t` are then unambiguous. Rows with ties AT `t`
  keep every value below `t` plus the lowest-index values equal to it.
  """
  c = sums.shape[0]
  kth = np.partition(sums, k - 1, axis=1)[:, k - 1]
  keep = sums <= kth[:, np.newaxis]
  tied = np.flatnonzero(np.count_nonzero(keep, axis=1) > k)
  if tied.size:
    sub = sums[tied]
    t = kth[tied, np.newaxis]
    below = sub < t
    at = sub == t
    need = k - np.count_nonzero(below, axis=1)
    rank_at = np.cumsum(at, axis=1, dtype=np.int32)
    keep[tied] = below | (at & (rank_at <= need[:, np.newaxis]))
  idx = np.nonzero(keep)[1].reshape(c, k)
  vals = np.take_along_axis(sums, idx, axis=1)
  order = np.lexsort((idx, vals), axis=1)
  return (np.take_along_axis(vals, order,
                             axis=1), np.take_along_axis(idx, order, axis=1))


def _knn_sums(q_num: np.ndarray, q_cat: np.ndarray, ref: _Reference, k: int,
              chunk: int) -> tuple[np.ndarray, np.ndarray]:
  """The k-NN float32 Gower SUMS and int64 reference indices per query row."""
  if not 1 <= k <= ref.n:
    raise ValueError(f"k must be in [1, {ref.n}] (the reference rows), got {k}")
  n_q = q_num.shape[0]
  sums = np.empty((n_q, k), dtype=np.float32)
  idx = np.empty((n_q, k), dtype=np.int64)
  for start, block in _iter_pair_sums(q_num, q_cat, ref, chunk):
    stop = start + block.shape[0]
    sums[start:stop], idx[start:stop] = _k_smallest(block, k)
  return sums, idx


def _prepare(q_num: Any, q_cat: Any, r_num: Any,
             r_cat: Any) -> tuple[np.ndarray, np.ndarray, _Reference]:
  """Validated query blocks and the prepared reference, shapes checked."""
  qn, qc = _as_blocks(q_num, q_cat, "query")
  rn, rc = _as_blocks(r_num, r_cat, "reference")
  if qn.shape[1] != rn.shape[1] or qc.shape[1] != rc.shape[1]:
    raise ValueError(
        "query and reference disagree on the feature layout: numeric "
        f"{qn.shape[1]} vs {rn.shape[1]}, categorical {qc.shape[1]} vs "
        f"{rc.shape[1]}")
  if qn.shape[1] + qc.shape[1] == 0:
    raise ValueError("no features to measure a Gower distance on")
  return qn, qc, _Reference(rn, rc)


def gower_knn(q_num: Any,
              q_cat: Any,
              r_num: Any,
              r_cat: Any,
              *,
              k: int,
              chunk: int = 64) -> tuple[np.ndarray, np.ndarray]:
  """Exact k nearest reference rows per query row under the Gower distance.

  `q_num`/`r_num` are float32 PIT blocks (`NaN` = NULL), `q_cat`/`r_cat`
  uint64 code blocks (`NULL_CODE` = NULL), as `GowerSpace.encode` returns.
  Returns `(distances, indices)`, both `(n_q, k)`: float64 Gower distances
  ascending, and int64 reference row indices, ties broken by the lower
  index. Queries are processed `effective_chunk(|R|, d, chunk)` rows at a
  time, so the working set stays within `chunk * |R| * d * 4` bytes and
  64 MB. Deterministic, and bit-identical for a query row whatever `chunk`.
  """
  qn, qc, ref = _prepare(q_num, q_cat, r_num, r_cat)
  sums, idx = _knn_sums(qn, qc, ref, k, chunk)
  return sums.astype(np.float64) / ref.n_features, idx


def density_coverage(real_num: Any,
                     real_cat: Any,
                     fake_num: Any,
                     fake_cat: Any,
                     *,
                     k: int = 5,
                     chunk: int = 64) -> tuple[float, float]:
  """Naeem et al. (2020) density and coverage of `fake` rows around `real` rows.

  Each real row `X_i` gets the closed ball `B(X_i, r_i)` whose radius is its
  k-th nearest-neighbour distance among the OTHER real rows (the `(k+1)`-th
  smallest distance with itself included, since its own distance is 0):

    density  = (1 / (k M)) sum_j sum_i 1[d(Y_j, X_i) <= r_i]
    coverage = (1 / N) sum_i 1[exists j: d(Y_j, X_i) <= r_i]

  The catalogue metrics (`row.density`, `row.coverage`) need `N == M`;
  `nn_privacy` guarantees it. Raises `ValueError` unless `N > k` and
  `M >= 1`.
  """
  fn, fc, ref = _prepare(fake_num, fake_cat, real_num, real_cat)
  if k < 1 or ref.n <= k:
    raise ValueError(
        f"density/coverage need k >= 1 and more than k = {k} real rows, got "
        f"{ref.n}")
  if fn.shape[0] < 1:
    raise ValueError("density/coverage need at least 1 fake row")
  radii = _knn_sums(ref.num, ref.cat, ref, k + 1, chunk)[0][:, k]
  in_balls = 0
  covered = np.zeros(ref.n, dtype=bool)
  for _, block in _iter_pair_sums(fn, fc, ref, chunk):
    inside = block <= radii[np.newaxis, :]
    in_balls += int(np.count_nonzero(inside))
    covered |= inside.any(axis=0)
  return in_balls / (k * fn.shape[0]), float(np.count_nonzero(covered) / ref.n)


def _nndr(dist: np.ndarray) -> np.ndarray:
  """`d1 / d2` per row; 1.0 where `d2 == 0` (two exact copies: nobody stands
  out)."""
  ratio = np.ones(dist.shape[0])
  np.divide(dist[:, 0], dist[:, 1], out=ratio, where=dist[:, 1] > 0)
  return ratio


@dataclass(frozen=True, eq=False)
class NNPrivacyResult:
  """Per-row nearest-neighbour distances and the aggregates built on them.

  `dcr_syn_r`/`dcr_syn_h`/`dcr_h_r` are each row's nearest Gower distance
  (synthetic to R, synthetic to H, holdout to R); `nndr_syn`/`nndr_h` the
  nearest over second-nearest distance to R. `closer_to_r` is the holdout
  share (ties one half) over `n_syn` synthetic rows. `density`/`coverage`
  are `None` when fewer than `k + 1` rows are available at equal n.
  """
  dcr_syn_r: np.ndarray
  dcr_syn_h: np.ndarray
  dcr_h_r: np.ndarray
  nndr_syn: np.ndarray
  nndr_h: np.ndarray
  closer_to_r: float
  n_syn: int
  density: float | None
  coverage: float | None


def nn_privacy(space: GowerSpace,
               r_rows: Sequence[Mapping[str, Any]],
               h_rows: Sequence[Mapping[str, Any]],
               syn_rows: Sequence[Mapping[str, Any]],
               *,
               k: int = 5) -> NNPrivacyResult:
  """The holdout DCR test, NNDR and density/coverage for one table.

  R and H are trimmed to the same size (their first `min(|R|, |H|)` rows;
  D3 makes both exchangeable ranks of the same source ordering). Distances:
  synthetic to R and to H (nearest) and H to R (nearest two; H and R are
  disjoint, so nothing is excluded). `closer_to_r = (#[d_R < d_H] + 0.5
  #[d_R == d_H]) / n_syn`. Density/coverage use `k` with real = R and
  fake = the first rows of the synthetic sample at equal n (`min(|R|,
  n_syn)`, R trimmed alike if the synthetic side is smaller); both are
  `None` when that n is `<= k`.

  Raises `ValueError` when R or H has fewer than 2 rows, the synthetic side
  is empty, or `k < 1` (the caller records the metrics as not evaluated).
  """
  if len(r_rows) < _MIN_SET_ROWS:
    raise ValueError("nearest-neighbour privacy needs at least 2 reference "
                     f"rows (R), got {len(r_rows)}")
  if len(h_rows) < _MIN_SET_ROWS:
    raise ValueError("nearest-neighbour privacy needs at least 2 holdout rows "
                     f"(H), got {len(h_rows)}")
  if not syn_rows:
    raise ValueError("nearest-neighbour privacy needs at least 1 synthetic row")
  if k < 1:
    raise ValueError(f"density/coverage k must be >= 1, got {k}")
  n_set = min(len(r_rows), len(h_rows))
  r_num, r_cat = space.encode(r_rows[:n_set])
  h_num, h_cat = space.encode(h_rows[:n_set])
  s_num, s_cat = space.encode(syn_rows)

  syn_r = gower_knn(s_num, s_cat, r_num, r_cat, k=_NNDR_K)[0]
  syn_h = gower_knn(s_num, s_cat, h_num, h_cat, k=1)[0][:, 0]
  h_r = gower_knn(h_num, h_cat, r_num, r_cat, k=_NNDR_K)[0]
  n_syn = s_num.shape[0]
  closer = (np.count_nonzero(syn_r[:, 0] < syn_h) +
            0.5 * np.count_nonzero(syn_r[:, 0] == syn_h)) / n_syn

  n_eq = min(n_set, n_syn)
  density: float | None = None
  coverage: float | None = None
  if n_eq > k:
    density, coverage = density_coverage(
        r_num[:n_eq], r_cat[:n_eq], s_num[:n_eq], s_cat[:n_eq], k=k)
  return NNPrivacyResult(
      dcr_syn_r=syn_r[:, 0],
      dcr_syn_h=syn_h,
      dcr_h_r=h_r[:, 0],
      nndr_syn=_nndr(syn_r),
      nndr_h=_nndr(h_r),
      closer_to_r=float(closer),
      n_syn=n_syn,
      density=density,
      coverage=coverage,
  )


def _quantile(values: np.ndarray, q: float) -> float | None:
  """`np.quantile` (linear interpolation) as a float; `None` when empty."""
  return float(np.quantile(values, q)) if values.size else None


def _ratio(num: float | None, den: float | None) -> float | None:
  """`num / den`, or `None` when either is missing or `den` is not positive."""
  if num is None or den is None or den <= 0.0:
    return None
  return num / den


def _hist_payload(values: np.ndarray) -> dict[str, Any]:
  """A `dcr_hist`/`nndr_hist` profile payload: 50 fixed bins over [0, 1]."""
  counts = bin_counts(values, np.asarray(_HIST_EDGES))
  return {
      "edges": list(_HIST_EDGES),
      "counts": [int(c) for c in counts],
      "p5": _quantile(values, _P5),
      "p50": _quantile(values, _P50),
      "n": int(values.size),
  }


def summarize_nn(res: NNPrivacyResult) -> dict[str, Any]:
  """Catalogue-ready values from one `NNPrivacyResult`.

  Keys named for their metric (`row.<key>`):
    - `dcr_train_holdout_share` with its Wilson (1927) interval
      `..._ci_low`/`..._ci_high` at `n_synthetic` (D5 gates on `ci_low`).
      Ties add one half, so the count `share * n` may be a half-integer;
      `wilson_interval` reads it only through `p = k / n`, and a score in
      {0, 1/2, 1} has variance at most `p (1 - p)`, so the interval stays
      conservative.
    - `dcr_p5_ratio = Q.05(dcr_syn_r) / Q.05(dcr_h_r)` and `nndr_p5_ratio =
      Q.05(nndr_syn) / Q.05(nndr_h)`, `None` when the holdout quantile is 0.
    - `density`, `coverage` (`None` when not computable).
  Plus `n_synthetic`, `n_reference` (= |R| = |H| after trimming), the p5/p50
  of every distance distribution (`<dist>_p5`, `<dist>_p50`; quantiles are
  linear-interpolated) and `profiles`: `dcr_hist`/`nndr_hist` payloads per
  side (`synthetic` = syn to R, `holdout` = H to R).
  """
  n_syn = res.n_syn
  # See the docstring: the half-integer count is exact through p = k / n.
  ci_low, ci_high = wilson_interval(cast(int, res.closer_to_r * n_syn), n_syn)
  summary: dict[str, Any] = {
      "n_synthetic": n_syn,
      "n_reference": int(res.dcr_h_r.size),
      "dcr_train_holdout_share": res.closer_to_r,
      "dcr_train_holdout_share_ci_low": ci_low,
      "dcr_train_holdout_share_ci_high": ci_high,
  }
  dists = {
      "dcr_syn_r": res.dcr_syn_r,
      "dcr_syn_h": res.dcr_syn_h,
      "dcr_h_r": res.dcr_h_r,
      "nndr_syn": res.nndr_syn,
      "nndr_h": res.nndr_h,
  }
  for name, values in dists.items():
    summary[f"{name}_p5"] = _quantile(values, _P5)
    summary[f"{name}_p50"] = _quantile(values, _P50)
  summary["dcr_p5_ratio"] = _ratio(summary["dcr_syn_r_p5"],
                                   summary["dcr_h_r_p5"])
  summary["nndr_p5_ratio"] = _ratio(summary["nndr_syn_p5"],
                                    summary["nndr_h_p5"])
  summary["density"] = res.density
  summary["coverage"] = res.coverage
  summary["profiles"] = {
      "dcr_hist": {
          "synthetic": _hist_payload(res.dcr_syn_r),
          "holdout": _hist_payload(res.dcr_h_r),
      },
      "nndr_hist": {
          "synthetic": _hist_payload(res.nndr_syn),
          "holdout": _hist_payload(res.nndr_h),
      },
  }
  return summary
