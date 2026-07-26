# WS1 — b2_library Memorization Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `b2_library` from copying real high-cardinality temporal/string values verbatim (11 CRITICAL `copy_ratio=1.0` columns in the 2026-07-20 E2E run), and stop paying ~230 s / 500+ GPU-s of vLLM ignition on runs that never call the LLM.

**Architecture:** Add a cardinality cap (20, mirroring B.1) to `b2_library`'s column classifier. Above the cap, temporal columns (DATE/TIME/TIMESTAMP types + date-shaped STRINGs) get a new `ColumnKind.TEMPORAL` sampled as novel values uniformly within the observed `[min, max]`; non-temporal high-card STRINGs are reclassified `FREE_TEXT` so they route through the existing LLM `FreeTextHook`. Separately, `VLLMModelClient` becomes lazily-igniting (`generate_json` calls its own idempotent `setup()` on first use) and the DoFn's eager client setup is removed.

**Tech Stack:** Pure Python + NumPy in `sdfb-core` (no Beam/GCP/torch); pytest in `packages/sdfb-tests`; the shared `engines/text_shapes.py` detector (`detect_temporal_format` already exists).

**Spec:** `docs/superpowers/specs/2026-07-20-e2e-remediation-rag-eval-evolution-design.md` §3 (WS1).

## Global Constraints

- `sdfb-core` must not import `apache_beam`, `google.cloud.*`, `vllm`, or `torch` (CLAUDE.md package map). `sdfb_core.observability.log_milestone` is allowed (already used by `b2_library/freetext.py`).
- Cardinality caps are exactly `20` (`_TEMPORAL_MAX_CATEGORIES = 20`, `_CATEGORICAL_MAX_CATEGORIES = 20`), mirroring `b1_rag/profile.py:64-65`.
- Non-printable warning threshold is exactly `0.05`; milestone name is `column_nonprintable`.
- Never silently drop or fall back: strict_freetext semantics are unchanged; every fallback keeps its existing `log_milestone` visibility.
- Test command on this laptop: `uv run --no-sync python3 -m pytest <path> -q` (project quirk — plain `uv run pytest` may re-sync). Full baseline: `uv run --no-sync python3 -m pytest packages/sdfb-tests -m "not gpu and not gcp" -q` must stay green (382+ tests).
- Lint: `uv run ruff check .` clean after every task.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Temporal conversion + novel-range sampler (`temporal.py`)

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/engines/b2_library/temporal.py`
- Test: `packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py` (new file)

**Interfaces:**
- Consumes: `sdfb_core.engines.text_shapes.detect_temporal_format(values) -> str | None` (exists).
- Produces (used by Tasks 2–4):
  - `classify_temporal_values(values: list[object]) -> tuple[str, str | None] | None` — `(value_type, strftime_fmt)` or `None`.
  - Value-type constants `VT_DATETIME`, `VT_DATETIME_UTC`, `VT_DATE`, `VT_TIME`, `VT_STR`.
  - `to_epoch(value, value_type, fmt) -> float`, `from_epoch(x, value_type, fmt) -> object`.
  - `sample_temporal(minimum, maximum, value_type, fmt, n, rng) -> list`.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py`:

```python
"""WS1: temporal conversion + novel-range sampling for b2_library.

The 2026-07-20 E2E run landed 8 high-cardinality temporal columns at
copy_ratio=1.0 because b2 resampled the observed value table verbatim.
These tests pin the replacement: epoch round-trips per value type and a
seeded uniform sampler that stays inside the observed [min, max].
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import numpy as np
from sdfb_core.engines.b2_library.temporal import (
    VT_DATE,
    VT_DATETIME,
    VT_DATETIME_UTC,
    VT_STR,
    VT_TIME,
    classify_temporal_values,
    from_epoch,
    sample_temporal,
    to_epoch,
)


def test_classify_date_strings():
    values = [f"2024-03-{d:02d}" for d in range(1, 25)]
    assert classify_temporal_values(values) == (VT_STR, "%Y-%m-%d")


def test_classify_native_types():
    aware = [datetime(2024, 1, 1, 12, 0, tzinfo=UTC), datetime(2024, 6, 1, tzinfo=UTC)]
    naive = [datetime(2024, 1, 1, 12, 0), datetime(2024, 6, 1)]
    dates = [date(2024, 1, 1), date(2024, 6, 1)]
    times = [time(9, 30), time(17, 45)]
    assert classify_temporal_values(aware) == (VT_DATETIME_UTC, None)
    assert classify_temporal_values(naive) == (VT_DATETIME, None)
    assert classify_temporal_values(dates) == (VT_DATE, None)
    assert classify_temporal_values(times) == (VT_TIME, None)


def test_classify_rejects_non_temporal_and_mixed():
    assert classify_temporal_values(["hello", "world"]) is None
    assert classify_temporal_values(["2024-01-01", "not a date"]) is None
    assert classify_temporal_values([datetime(2024, 1, 1), date(2024, 1, 2)]) is None
    assert classify_temporal_values([]) is None


def test_epoch_round_trip_every_value_type():
    cases = [
        (datetime(2024, 3, 5, 6, 7, 8, tzinfo=UTC), VT_DATETIME_UTC, None),
        (datetime(2024, 3, 5, 6, 7, 8), VT_DATETIME, None),
        (date(2024, 3, 5), VT_DATE, None),
        (time(6, 7, 8), VT_TIME, None),
        ("2024-03-05", VT_STR, "%Y-%m-%d"),
        ("2024-03-05 06:07:08", VT_STR, "%Y-%m-%d %H:%M:%S"),
    ]
    for value, vt, fmt in cases:
        assert from_epoch(to_epoch(value, vt, fmt), vt, fmt) == value


def test_sample_temporal_in_range_novel_and_seeded():
    lo = to_epoch("2024-01-01", VT_STR, "%Y-%m-%d")
    hi = to_epoch("2024-12-01", VT_STR, "%Y-%m-%d")
    a = sample_temporal(lo, hi, VT_STR, "%Y-%m-%d", 200, np.random.default_rng(7))
    b = sample_temporal(lo, hi, VT_STR, "%Y-%m-%d", 200, np.random.default_rng(7))
    assert a == b  # seeded determinism
    parsed = [datetime.strptime(v, "%Y-%m-%d") for v in a]
    assert all(datetime(2024, 1, 1) <= p <= datetime(2024, 12, 1) for p in parsed)
    assert len(set(a)) > 20  # novel spread, not a handful of repeats


def test_sample_temporal_degenerate_bounds():
    assert sample_temporal(None, None, VT_STR, "%Y-%m-%d", 3, np.random.default_rng(0)) == [None] * 3
    lo = to_epoch("2024-05-05", VT_STR, "%Y-%m-%d")
    assert sample_temporal(lo, lo, VT_STR, "%Y-%m-%d", 2, np.random.default_rng(0)) == ["2024-05-05"] * 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdfb_core.engines.b2_library.temporal'`

- [ ] **Step 3: Write the implementation**

Create `packages/sdfb-core/src/sdfb_core/engines/b2_library/temporal.py`:

```python
"""Temporal profiling + novel-range sampling for B.2 (WS1, spec §3a).

High-cardinality DATE/TIME/TIMESTAMP columns (and date-shaped STRING
columns) must never be resampled verbatim from the observed value table —
the 2026-07-20 E2E run landed 8 such columns at copy_ratio=1.0. Instead
the profile records the observed [min, max] as epoch floats and the
backend samples uniformly within it, rendering back to the column's
native value type / observed string format. Mirrors B.1's temporal
novel-range behavior (b1_rag/profile.py `_profile_temporal`).

Pure stdlib + the shared text_shapes detector. No Beam, no GCP, no torch.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING

from sdfb_core.engines.text_shapes import detect_temporal_format

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

# ColumnProfile.temporal_value_type values. Epoch units per type:
# seconds since 1970 (datetimes and rendered strings), proleptic ordinal
# days (dates), seconds-of-day (times). from_epoch mirrors to_epoch, so
# the unit never leaks outside this module.
VT_DATETIME = "datetime"          # tz-naive datetime objects
VT_DATETIME_UTC = "datetime_utc"  # tz-aware datetime objects (rendered UTC)
VT_DATE = "date"                  # datetime.date objects
VT_TIME = "time"                  # datetime.time objects
VT_STR = "str"                    # strings in one strftime format

_EPOCH_NAIVE = datetime(1970, 1, 1)
_MAX_SECONDS_OF_DAY = 86_399.999_999


def classify_temporal_values(values: list[object]) -> tuple[str, str | None] | None:
    """``(value_type, strftime_format)`` when EVERY value is uniformly
    temporal, else ``None`` (mixed types/formats stay on their existing
    route — same all-or-nothing contract as ``detect_temporal_format``)."""
    if not values:
        return None
    first = values[0]
    if isinstance(first, datetime):  # before date: datetime IS a date
        if not all(isinstance(v, datetime) for v in values):
            return None
        aware = first.tzinfo is not None
        if any((v.tzinfo is not None) != aware for v in values):
            return None
        return (VT_DATETIME_UTC if aware else VT_DATETIME, None)
    if isinstance(first, date):
        if not all(isinstance(v, date) and not isinstance(v, datetime) for v in values):
            return None
        return (VT_DATE, None)
    if isinstance(first, time):
        if not all(isinstance(v, time) for v in values):
            return None
        return (VT_TIME, None)
    if isinstance(first, str):
        if not all(isinstance(v, str) for v in values):
            return None
        fmt = detect_temporal_format(values)
        return (VT_STR, fmt) if fmt else None
    return None


def to_epoch(value: object, value_type: str, fmt: str | None) -> float:
    if value_type == VT_DATETIME_UTC:
        return value.timestamp()
    if value_type == VT_DATETIME:
        return (value - _EPOCH_NAIVE).total_seconds()
    if value_type == VT_DATE:
        return float(value.toordinal())
    if value_type == VT_TIME:
        return (
            value.hour * 3600 + value.minute * 60 + value.second
            + value.microsecond / 1e6
        )
    return (datetime.strptime(value, fmt) - _EPOCH_NAIVE).total_seconds()


def from_epoch(x: float, value_type: str, fmt: str | None) -> object:
    if value_type == VT_DATETIME_UTC:
        return datetime.fromtimestamp(x, tz=UTC)
    if value_type == VT_DATETIME:
        return _EPOCH_NAIVE + timedelta(seconds=x)
    if value_type == VT_DATE:
        return date.fromordinal(round(x))
    if value_type == VT_TIME:
        s = max(0.0, min(x, _MAX_SECONDS_OF_DAY))
        whole = int(s)
        return time(whole // 3600, (whole % 3600) // 60, whole % 60,
                    int(round((s - whole) * 1e6)))
    return (_EPOCH_NAIVE + timedelta(seconds=x)).strftime(fmt)


def sample_temporal(
    minimum: float | None,
    maximum: float | None,
    value_type: str,
    fmt: str | None,
    n: int,
    rng: np.random.Generator,
) -> list:
    """``n`` novel values uniformly within the observed epoch bounds."""
    if minimum is None or maximum is None:
        return [None] * n
    if maximum <= minimum:
        return [from_epoch(minimum, value_type, fmt)] * n
    draws = rng.uniform(minimum, maximum, size=n)
    return [from_epoch(float(x), value_type, fmt) for x in draws]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py -q`
Expected: 6 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check packages/sdfb-core packages/sdfb-tests
git add packages/sdfb-core/src/sdfb_core/engines/b2_library/temporal.py \
        packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py
git commit -m "feat(b2): temporal epoch conversion + novel-range sampler (WS1 §3a)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `ColumnKind.TEMPORAL` + cardinality caps in the classifier

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/fidelity.py` (`ColumnKind`, `ColumnProfile`, `_classify`, `profile_column`)
- Test: `packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py` (append)

**Interfaces:**
- Consumes: Task 1's `classify_temporal_values`, `to_epoch`.
- Produces (used by Tasks 3–4):
  - `ColumnKind.TEMPORAL` enum member (value `"temporal"`).
  - `ColumnProfile.temporal_value_type: str | None` and `ColumnProfile.temporal_format: str | None`; for TEMPORAL profiles `minimum`/`maximum` hold epoch floats.
  - Module constants `_TEMPORAL_MAX_CATEGORIES = 20`, `_CATEGORICAL_MAX_CATEGORIES = 20`, `_TEMPORAL_BQ_TYPES`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py`:

```python
from datetime import timedelta

from sdfb_core.contracts import TableSchema
from sdfb_core.engines.b2_library.fidelity import ColumnKind, profile_table

_SCHEMA = {
    "table_info": {"table_id": "demo.events"},
    "schema": [
        {"name": "event_id", "type": "INT64", "mode": "REQUIRED"},
        {"name": "ts", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "ts_low", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "d_str", "type": "STRING", "mode": "REQUIRED"},
        {"name": "code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "enum_col", "type": "STRING", "mode": "REQUIRED", "max_length": 8},
    ],
    "primary_keys": ["event_id"],
}


def _reference_rows(n: int = 100) -> list[dict]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        {
            "event_id": i,
            # 60 distinct tz-aware timestamps (> cap 20) → TEMPORAL
            "ts": base + timedelta(hours=i % 60),
            # 10 distinct timestamps (≤ cap) → CATEGORICAL (enum-in-disguise)
            "ts_low": base + timedelta(days=i % 10),
            # 30 distinct date strings, ratio 0.3 (< 0.9) → TEMPORAL via shape
            "d_str": f"2024-06-{(i % 30) + 1:02d}",
            # 30 distinct short non-date strings, ratio 0.3 → FREE_TEXT (new cap route)
            "code": f"br {i % 30:03d} x",
            # 5 distinct → CATEGORICAL as before
            "enum_col": ["A", "B", "C", "D", "E"][i % 5],
        }
        for i in range(n)
    ]


def test_classifier_caps_route_families_correctly():
    schema = TableSchema.model_validate(_SCHEMA)
    profiles = profile_table(schema, _reference_rows())
    assert profiles["ts"].kind is ColumnKind.TEMPORAL
    assert profiles["ts"].temporal_value_type == VT_DATETIME_UTC
    assert profiles["ts"].minimum is not None
    assert profiles["ts"].maximum > profiles["ts"].minimum
    assert profiles["ts_low"].kind is ColumnKind.CATEGORICAL
    assert profiles["d_str"].kind is ColumnKind.TEMPORAL
    assert profiles["d_str"].temporal_value_type == VT_STR
    assert profiles["d_str"].temporal_format == "%Y-%m-%d"
    assert profiles["code"].kind is ColumnKind.FREE_TEXT
    assert profiles["enum_col"].kind is ColumnKind.CATEGORICAL


def test_unparseable_high_card_temporal_demotes_to_categorical():
    # TIMESTAMP-typed column whose values are mixed strings the detector
    # rejects → stays CATEGORICAL (in-support) instead of crashing.
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.weird"},
            "schema": [{"name": "w", "type": "TIMESTAMP", "mode": "REQUIRED"}],
            "primary_keys": None,
        }
    )
    rows = [{"w": f"junk-{i}" if i % 2 else f"2024-01-{(i % 28) + 1:02d}"} for i in range(50)]
    profiles = profile_table(schema, rows)
    assert profiles["w"].kind is ColumnKind.CATEGORICAL
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py -q`
Expected: the two new tests FAIL (`AttributeError: TEMPORAL` / kind mismatch); Task 1 tests still pass.

- [ ] **Step 3: Implement in `fidelity.py`**

Add the import (below the existing `text_shapes` import):

```python
from sdfb_core.engines.b2_library.temporal import classify_temporal_values, to_epoch
```

Add constants next to `_HIGH_CARDINALITY_RATIO` / `_FREE_TEXT_MIN_LEN`:

```python
# Cardinality caps (mirror b1_rag/profile.py:64-65). At or below the cap a
# discrete column is an enum-in-disguise and verbatim empirical resampling
# is the intended fidelity primitive. Above it, resampling IS memorization
# (2026-07-20 E2E: 11 columns at copy_ratio=1.0) — temporal columns jitter
# within the observed range, other strings go to the LLM free-text hook.
_TEMPORAL_MAX_CATEGORIES = 20
_CATEGORICAL_MAX_CATEGORIES = 20
_TEMPORAL_BQ_TYPES = frozenset({"DATE", "DATETIME", "TIME", "TIMESTAMP"})
```

Add `TEMPORAL` to `ColumnKind` (after `CATEGORICAL`, updating the docstring list):

```python
    TEMPORAL = "temporal"
```

with docstring line: `- ``TEMPORAL``: high-cardinality date/time → novel-range jitter within [min, max].`

Add the two fields at the end of `ColumnProfile` (after `identifier_shape`):

```python
    # TEMPORAL: how to render sampled epoch floats back into values.
    # minimum/maximum hold epoch floats (units per temporal.py) for this kind.
    temporal_value_type: str | None = None
    temporal_format: str | None = None
```

Rewrite the tail of `_classify` (everything from the `_STRINGY_BQ_TYPES` branch down) as:

```python
    if field.bq_type in _TEMPORAL_BQ_TYPES:
        if distinct <= _TEMPORAL_MAX_CATEGORIES:
            return ColumnKind.CATEGORICAL  # enum-in-disguise (load-date partitions)
        return ColumnKind.TEMPORAL

    if field.bq_type in _STRINGY_BQ_TYPES:
        # JSON columns always go to the free-text hook — empirical resampling
        # of structured blobs is meaningless.
        if field.bq_type == "JSON":
            return ColumnKind.FREE_TEXT
        strs = [str(v) for v in non_null_values]
        cardinality_ratio = distinct / max(len(strs), 1)
        mean_len = sum(len(s) for s in strs) / max(len(strs), 1)
        if cardinality_ratio >= _HIGH_CARDINALITY_RATIO or mean_len >= _FREE_TEXT_MIN_LEN:
            return ColumnKind.FREE_TEXT
        if distinct <= _CATEGORICAL_MAX_CATEGORIES:
            return ColumnKind.CATEGORICAL
        # Above the cap: date-shaped strings jitter as TEMPORAL; anything
        # else is high-cardinality discrete text the LLM must synthesize —
        # resampling it verbatim is the 2026-07-20 memorization defect.
        if classify_temporal_values(strs) is not None:
            return ColumnKind.TEMPORAL
        return ColumnKind.FREE_TEXT

    # Everything else (BOOL handled above): categorical over the observed pool.
    return ColumnKind.CATEGORICAL
```

In `profile_column`, insert a TEMPORAL branch between the `NUMERIC` and `FREE_TEXT` blocks — note it *demotes* to CATEGORICAL when the values don't parse uniformly:

```python
    if kind is ColumnKind.TEMPORAL:
        spec = classify_temporal_values(non_null)
        if spec is None:
            # BQ-typed temporal whose observed values are mixed/unparseable:
            # stay in-support rather than guessing an epoch mapping.
            kind = ColumnKind.CATEGORICAL
        else:
            value_type, fmt = spec
            epochs = [to_epoch(v, value_type, fmt) for v in non_null]
            return ColumnProfile(
                name=field.name,
                bq_type=field.bq_type,
                kind=ColumnKind.TEMPORAL,
                nullable=nullable,
                null_fraction=null_fraction,
                minimum=min(epochs),
                maximum=max(epochs),
                temporal_value_type=value_type,
                temporal_format=fmt,
            )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py packages/sdfb-tests/tests/unit/engines/test_b2_library.py -q`
Expected: all pass (the existing b2 fixture is 4 rows — never above the caps).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check packages/sdfb-core packages/sdfb-tests
git add -u && git add packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py
git commit -m "feat(b2): cardinality caps + ColumnKind.TEMPORAL in _classify (WS1 §3a)

Above 20 distinct: DATE/TIME/TIMESTAMP and date-shaped STRINGs jitter as
TEMPORAL; other high-card STRINGs reroute to the LLM free-text hook.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Backend sampling for TEMPORAL profiles

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/backends.py` (`EmpiricalBackend._sample_one`, `SdgxBackend._reference_frame`, `SdgxBackend.sample_columns`)
- Test: `packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py` (append)

**Interfaces:**
- Consumes: `ColumnKind.TEMPORAL`, `ColumnProfile.temporal_value_type/temporal_format` (Task 2); `sample_temporal` (Task 1).
- Produces: `EmpiricalBackend.sample_columns` / `SdgxBackend.sample_columns` now emit novel temporal values for TEMPORAL profiles; CTGAN never sees temporal columns.

- [ ] **Step 1: Write the failing tests**

Append to `test_b2_temporal.py`:

```python
import pandas as pd
from sdfb_core.engines.b2_library.backends import (
    EmpiricalBackend,
    SdgxBackend,
    _samplable_profiles,
)


def test_empirical_backend_samples_novel_in_range_temporal():
    schema = TableSchema.model_validate(_SCHEMA)
    rows = _reference_rows()
    profiles = profile_table(schema, rows)
    backend = EmpiricalBackend()
    backend.fit(rows, profiles)
    out = backend.sample_columns(500, np.random.default_rng(11))

    observed_ts = {r["ts"] for r in rows}
    sampled_ts = [v for v in out["ts"] if v is not None]
    assert all(isinstance(v, datetime) and v.tzinfo is not None for v in sampled_ts)
    lo, hi = min(observed_ts), max(observed_ts)
    assert all(lo <= v <= hi for v in sampled_ts)
    # Novel-range jitter, not the verbatim observed table (60 distinct
    # observed instants in a 59-hour continuous range → collisions ≈ 0).
    copy_ratio = sum(v in observed_ts for v in sampled_ts) / len(sampled_ts)
    assert copy_ratio < 0.3

    observed_d = {r["d_str"] for r in rows}
    sampled_d = [v for v in out["d_str"] if v is not None]
    assert all(datetime.strptime(v, "%Y-%m-%d") for v in sampled_d)
    # 30 observed days inside a 29-day span is a saturated keyspace, so
    # membership is unavoidable — the defect was FREQUENCY copying; assert
    # the draw is range-uniform, not the empirical table (distinct spread).
    assert len(set(sampled_d)) >= 20

    # Below-cap timestamp column stays empirical (in observed support).
    observed_low = {r["ts_low"] for r in rows}
    assert set(v for v in out["ts_low"] if v is not None) <= observed_low


def test_sdgx_reference_frame_excludes_temporal_columns():
    schema = TableSchema.model_validate(_SCHEMA)
    rows = _reference_rows()
    profiles = profile_table(schema, rows)
    backend = SdgxBackend()
    backend._profiles = _samplable_profiles(profiles)
    frame = backend._reference_frame(rows, pd)
    assert "ts" not in frame.columns and "d_str" not in frame.columns
    assert "enum_col" in frame.columns and "event_id" in frame.columns
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py -q`
Expected: the two new tests FAIL (TEMPORAL falls into `_sample_one`'s `else` → all-None; frame still contains `ts`).

- [ ] **Step 3: Implement in `backends.py`**

Add to the imports:

```python
from sdfb_core.engines.b2_library.temporal import sample_temporal
```

In `EmpiricalBackend._sample_one`, add a branch between the `CATEGORICAL` branch and the final `else`:

```python
        elif p.kind is ColumnKind.TEMPORAL:
            values = sample_temporal(
                p.minimum, p.maximum, p.temporal_value_type, p.temporal_format, n, rng
            )
```

In `SdgxBackend._reference_frame`, change the keep-set line from `keep = set(self._profiles)` to:

```python
        # Temporal columns are jitter-sampled from their profile, never fed
        # to CTGAN — fitting raw timestamps makes the model resample the
        # observed table (the 2026-07-20 memorization defect).
        keep = {
            name for name, p in self._profiles.items()
            if p.kind is not ColumnKind.TEMPORAL
        }
```

In `SdgxBackend.sample_columns`, change the per-profile loop body to handle TEMPORAL before consulting the sampled DataFrame:

```python
        for name, p in self._profiles.items():
            if p.kind is ColumnKind.TEMPORAL:
                out[name] = sample_temporal(
                    p.minimum, p.maximum, p.temporal_value_type, p.temporal_format, n, rng
                )
            elif name in sampled.columns:
                out[name] = list(sampled[name])
            else:
                # CTGAN dropped a column (e.g. constant) — fill from profile.
                out[name] = _fill_from_profile(p, n)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check packages/sdfb-core packages/sdfb-tests
git add -u
git commit -m "feat(b2): jitter-sample TEMPORAL profiles in both backends (WS1 §3a)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Fidelity clamps + engine-level integration test

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/fidelity.py` (`enforce_value`, `_representative`)
- Test: `packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1–3; `B2LibraryEngine`, `FakeModelClient` (exist).
- Produces: `enforce_value` passes TEMPORAL values through; `_representative` renders `minimum` for TEMPORAL. End-to-end b2 engine yields novel temporal values.

- [ ] **Step 1: Write the failing tests**

Append to `test_b2_temporal.py`:

```python
from sdfb_beam.handlers.fake_client import FakeModelClient
from sdfb_core.engines import GenerationConfig, GenerationContext
from sdfb_core.engines.b2_library import B2LibraryEngine
from sdfb_core.engines.b2_library.fidelity import _representative, enforce_value


def test_enforce_value_passes_temporal_through_and_representative_renders():
    schema = TableSchema.model_validate(_SCHEMA)
    profiles = profile_table(schema, _reference_rows())
    p = profiles["d_str"]
    assert enforce_value(p, "2024-06-15") == "2024-06-15"
    rep = _representative(p)
    assert datetime.strptime(rep, "%Y-%m-%d")  # renders minimum, parseable


def test_engine_end_to_end_yields_novel_temporal_rows():
    rows = _reference_rows()
    ctx = GenerationContext(
        table_schema=TableSchema.model_validate(_SCHEMA),
        reference_rows=rows,
        reference_digest="temporal-digest",
        pipeline_run_id="ws1-temporal-run",
    )
    engine = B2LibraryEngine(use_sdgx=False)
    engine.setup(FakeModelClient(responses=rows), ctx)
    records = list(engine.generate_batch(50, GenerationConfig(seed=3, batch_size=50)))
    assert len(records) == 50  # no rows dropped by record-model validation

    dumped = [r.model_dump(mode="python") for r in records]
    observed_ts = {r["ts"] for r in rows}
    sampled_ts = [d["ts"] for d in dumped]
    copy_ratio = sum(v in observed_ts for v in sampled_ts) / len(sampled_ts)
    assert copy_ratio < 0.3  # the 2026-07-20 defect was 1.0
    assert all(datetime.strptime(d["d_str"], "%Y-%m-%d") for d in dumped)
    assert all(isinstance(d["code"], str) and d["code"] for d in dumped)  # hook ran
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py -q`
Expected: FAIL — `enforce_value` snaps TEMPORAL values through the CATEGORICAL/`_representative(None)` path (TEMPORAL has empty `categories`), and/or end-to-end rows drop at validation.

- [ ] **Step 3: Implement in `fidelity.py`**

Add the import of `from_epoch` (extend the Task 2 import line):

```python
from sdfb_core.engines.b2_library.temporal import (
    classify_temporal_values,
    from_epoch,
    to_epoch,
)
```

In `enforce_value`, insert between the NUMERIC and CATEGORICAL branches:

```python
    if profile.kind is ColumnKind.TEMPORAL:
        # Sampler-owned: values are rendered from in-range epoch draws by
        # construction; re-parsing them here would just repeat temporal.py.
        return value
```

In `_representative`, insert before the CATEGORICAL branch:

```python
    if profile.kind is ColumnKind.TEMPORAL and profile.minimum is not None:
        return from_epoch(
            profile.minimum, profile.temporal_value_type, profile.temporal_format
        )
```

- [ ] **Step 4: Run to verify pass, then the whole engines suite**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines -q`
Expected: all pass (contract tests + b1 + b2 + shapes).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check packages/sdfb-core packages/sdfb-tests
git add -u
git commit -m "feat(b2): TEMPORAL fidelity clamps + end-to-end novelty test (WS1 §3a)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Lazy vLLM ignition

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/handlers/vllm_client.py` (`setup`, `generate_json`)
- Modify: `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py` (`GenerateRecordsDoFn.setup` — remove the eager client-setup block)
- Modify: `packages/sdfb-tests/tests/unit/dofns/test_generate_client_lifecycle.py` (rewrite)
- Modify: `packages/sdfb-tests/tests/unit/handlers/test_vllm_client.py` (replace `test_generate_json_before_setup_raises`; check `test_setup_emits_milestones` still passes)

**Interfaces:**
- Consumes: `VLLMModelClient.setup()` is already idempotent + `_SETUP_LOCK`-serialized (`vllm_client.py:210-287`); `FreeTextHook`/b1 pool build call `generate_json` under `strict_freetext=True` for real clients, so a setup failure still raises out loudly.
- Produces: `VLLMModelClient.generate_json()` self-ignites when `_client is None`; the `model_client_setup_start`/`model_client_setup_done` milestones move from the DoFn into `VLLMModelClient.setup()`; `GenerateRecordsDoFn.setup()` no longer calls `client.setup()` (teardown unchanged).

**Semantic shift (intentional, documented):** a vLLM boot failure now surfaces at the first LLM call instead of at DoFn setup. For b1 that is still inside `DoFn.setup()` (pool build) → worker crash, as today. For b2 it is inside the first `generate_batch` → batches route to the DLQ as `engine_failure` (BLOCKER) and the `blocker_failure_ratio` gate fails the run. Nothing becomes silent: `strict_freetext` still re-raises, `freetext_llm_fallback` milestones still fire for non-strict fakes.

- [ ] **Step 1: Rewrite the lifecycle tests (failing first)**

Replace the entire contents of `packages/sdfb-tests/tests/unit/dofns/test_generate_client_lifecycle.py` with:

```python
"""GenerateRecordsDoFn + lazy ModelClient ignition (WS1 §3b).

History: the 2026-07-10 E2E defect was nobody calling
`VLLMModelClient.setup()` → silent 100% memorization. The first fix made
the DoFn call it eagerly — which the 2026-07-20 b2 run showed wastes
~230 s / 519 GPU-s when no column ever reaches the LLM. The contract is
now: the CLIENT self-ignites on first `generate_json()` (idempotent,
lock-serialized), the DoFn never ignites eagerly, and teardown remains
the DoFn's job. Loud-failure is preserved: a boot error raises out of the
first LLM call instead of being swallowed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest
from sdfb_beam.dofns import generate as generate_mod
from sdfb_beam.dofns.generate import GenerateRecordsDoFn


class _Record:
    def __init__(self, i: int) -> None:
        self._i = i

    def model_dump(self, mode="python"):
        return {"i": self._i}


class _LlmEngine:
    """Engine that calls the LLM during setup (the B.1 shape)."""

    events: ClassVar[list[str]] = []

    def setup(self, model_client, ctx):
        _LlmEngine.events.append("engine_setup")
        model_client.generate_json(prompt="p", json_schema={})

    def generate_batch(self, n, cfg):
        for i in range(n):
            yield _Record(i)

    def teardown(self):
        _LlmEngine.events.append("engine_teardown")


class _NoLlmEngine:
    """Engine that never touches the LLM (b2 with only empirical/
    identifier/jitter columns — the 2026-07-20 wasted-ignition case)."""

    events: ClassVar[list[str]] = []

    def setup(self, model_client, ctx):
        _NoLlmEngine.events.append("engine_setup")

    def generate_batch(self, n, cfg):
        for i in range(n):
            yield _Record(i)

    def teardown(self):
        _NoLlmEngine.events.append("engine_teardown")


class _LazyClient:
    """The new VLLMModelClient shape: generate_json self-ignites."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.ready = False

    def setup(self):
        self.events.append("client_setup")
        self.ready = True

    def teardown(self):
        self.events.append("client_teardown")
        self.ready = False

    def generate_json(self, prompt, json_schema, **kw):
        if not self.ready:
            self.setup()
        return [{}]


class _BoomLazyClient(_LazyClient):
    """Boot failure at first LLM use must raise loudly, never degrade."""

    def setup(self):
        raise RuntimeError("vllm boom")


class _NoLifecycleClient:
    """FakeModelClient shape — no setup()/teardown()."""

    def generate_json(self, prompt, json_schema, **kw):
        return [{}]


def _ctx():
    return SimpleNamespace(
        embedder_uri="",
        table_schema=SimpleNamespace(columns=[]),
        identity_columns=[],
        pipeline_run_id="lifecycle-test",
    )


def _dofn(client, engine_cls, monkeypatch):
    monkeypatch.setattr(generate_mod, "get_engine", lambda name: engine_cls)
    return GenerateRecordsDoFn(
        engine_name="stub", model_client=client, ctx=_ctx(), seed=7
    )


def test_no_ignition_when_engine_never_calls_llm(monkeypatch):
    _NoLlmEngine.events = []
    client = _LazyClient(_NoLlmEngine.events)
    dofn = _dofn(client, _NoLlmEngine, monkeypatch)
    dofn.setup()
    rows = list(dofn.process({"n": 2, "batch_id": 0}))
    dofn.teardown()
    assert len(rows) == 2
    # The whole point of WS1 §3b: no client_setup anywhere in the run.
    assert _NoLlmEngine.events == [
        "engine_setup",
        "engine_teardown",
        "client_teardown",
    ]


def test_lazy_ignition_fires_during_first_llm_call(monkeypatch):
    _LlmEngine.events = []
    client = _LazyClient(_LlmEngine.events)
    dofn = _dofn(client, _LlmEngine, monkeypatch)
    dofn.setup()
    dofn.teardown()
    assert _LlmEngine.events == [
        "engine_setup",
        "client_setup",
        "engine_teardown",
        "client_teardown",
    ]


def test_client_without_lifecycle_still_works(monkeypatch):
    _NoLlmEngine.events = []
    dofn = _dofn(_NoLifecycleClient(), _NoLlmEngine, monkeypatch)
    dofn.setup()
    rows = list(dofn.process({"n": 2, "batch_id": 0}))
    dofn.teardown()
    assert len(rows) == 2
    assert _NoLlmEngine.events == ["engine_setup", "engine_teardown"]


def test_boot_failure_raises_out_of_first_llm_call(monkeypatch):
    """B.1 shape: the engine's setup calls the LLM, so a boot failure still
    crashes DoFn.setup() — same blast radius as the old eager contract."""
    _LlmEngine.events = []
    client = _BoomLazyClient(_LlmEngine.events)
    dofn = _dofn(client, _LlmEngine, monkeypatch)
    with pytest.raises(RuntimeError, match="vllm boom"):
        dofn.setup()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/dofns/test_generate_client_lifecycle.py -q`
Expected: `test_no_ignition_when_engine_never_calls_llm` FAILS (the DoFn still eagerly calls `client.setup()`, so `client_setup` appears in events). The other tests pass.

- [ ] **Step 3: Remove the eager ignition from `generate.py`**

In `GenerateRecordsDoFn.setup()`, delete the whole block at lines 89–107 (the comment starting `# The vLLM client owns a server subprocess…` through the `client_setup()` milestone pair) and replace it with:

```python
        # LLM ignition is LAZY (WS1 §3b): VLLMModelClient.generate_json()
        # calls its own idempotent, lock-serialized setup() on first use, so
        # a run whose columns never reach the LLM (b2 with only empirical/
        # identifier/jitter columns) never pays the vLLM bring-up — the
        # 2026-07-20 run spent 519 GPU-s igniting a server that generated
        # nothing. Failure stays loud: under strict_freetext a boot error
        # raises out of the first pool call. teardown() remains unconditional.
```

- [ ] **Step 4: Make `VLLMModelClient` self-igniting with the moved milestones**

In `vllm_client.py` `generate_json()`, replace the `if self._client is None: raise RuntimeError(...)` guard (lines ~373-378) with:

```python
        if self._client is None:
            # Lazy ignition (WS1 §3b): the first real LLM call brings the
            # server up. setup() is idempotent and _SETUP_LOCK-serialized,
            # so concurrent DoFn threads still share one server.
            self.setup()
```

In `setup()`, inside the `with _SETUP_LOCK:` block, immediately after the second idempotence check (`if self._client is not None: return`) and the existing `t0 = time.monotonic()` line, add:

```python
            log_milestone("model_client_setup_start", client=type(self).__name__)
```

Then add a done-milestone on **both** exit paths:
- in the server-reuse branch, right after the existing `log_milestone("vllm_reuse", ...)` call and before its `return`:

```python
                log_milestone(
                    "model_client_setup_done",
                    seconds=round(time.monotonic() - t0, 1),
                )
```

- at the end of the spawn path, right after the existing `log_milestone("vllm_ready", ...)` line:

```python
            log_milestone(
                "model_client_setup_done", seconds=round(time.monotonic() - t0, 1)
            )
```

- [ ] **Step 5: Update the vLLM client unit tests**

In `packages/sdfb-tests/tests/unit/handlers/test_vllm_client.py`, replace the function `test_generate_json_before_setup_raises` (line ~225 — it holds both `pytest.raises(RuntimeError, match="before setup")` blocks) with:

```python
def test_generate_json_self_ignites_when_not_set_up(monkeypatch):
    """WS1 §3b: generate_json() must call setup() itself instead of raising.
    A ready client is borrowed to stand in for what setup() would build."""
    ready = _client_with_fake_openai([json.dumps({"values": ["x"]})])
    c = VLLMModelClient(model_uri="gs://bucket/m/v1/")
    calls: list[str] = []

    def fake_setup():
        calls.append("setup")
        c._client = ready._client
        c._served_model_name = ready._served_model_name

    monkeypatch.setattr(c, "setup", fake_setup)
    out = c.generate_json(prompt="p", json_schema={"type": "object"})
    assert calls == ["setup"]
    assert out == [{"values": ["x"]}]
```

Then check `test_setup_emits_milestones` (line ~275): it asserts on the milestone set emitted by `setup()`. Extend its expected names with `model_client_setup_start` and `model_client_setup_done` if it enumerates exhaustively (read the assertion; if it only checks a subset via `in`, no change is needed).

- [ ] **Step 6: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/dofns packages/sdfb-tests/tests/unit/handlers -q`
Expected: all pass.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check packages/sdfb-beam packages/sdfb-tests
git add -u
git commit -m "feat: lazy vLLM ignition — client self-setup on first LLM call (WS1 §3b)

DoFn no longer ignites eagerly; runs with no LLM-needing columns skip the
~230 s / 500+ GPU-s bring-up entirely. Milestone chain moves into
VLLMModelClient.setup(); loud-failure semantics preserved via
strict_freetext.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Non-printable-data guard milestone

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/fidelity.py` (`profile_column` + helpers)
- Test: `packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py` (append)

**Interfaces:**
- Consumes: `sdfb_core.observability.log_milestone(name, *, level, **fields)`.
- Produces: profiling a STRING/BYTES column whose values carry control characters (≥ 5% of values) emits one `SDFB_MILESTONE name=column_nonprintable column=<name> ratio=<r>` WARNING — the COL_048 BYTES-as-STRING signature becomes visible per run.

- [ ] **Step 1: Write the failing tests**

Append to `test_b2_temporal.py`:

```python
import logging


def _one_string_col_schema(name: str = "blob") -> TableSchema:
    return TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.blobs"},
            "schema": [{"name": name, "type": "STRING", "mode": "REQUIRED"}],
            "primary_keys": None,
        }
    )


def test_nonprintable_string_column_emits_milestone(caplog):
    # The COL_048 signature: C0/C1 control bytes round-tripped as STRING.
    rows = [{"blob": f"S1\x8e\x9d\x07x{i}"} for i in range(10)]
    with caplog.at_level(logging.WARNING):
        profile_table(_one_string_col_schema(), rows)
    hits = [r for r in caplog.records if "column_nonprintable" in r.getMessage()]
    assert hits and "column=blob" in hits[0].getMessage()


def test_accented_text_does_not_trigger_nonprintable(caplog):
    rows = [{"blob": f"Städte-Übersicht émission {i}"} for i in range(10)]
    with caplog.at_level(logging.WARNING):
        profile_table(_one_string_col_schema(), rows)
    assert not [r for r in caplog.records if "column_nonprintable" in r.getMessage()]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py -q`
Expected: first new test FAILS (no milestone emitted).

- [ ] **Step 3: Implement in `fidelity.py`**

Add imports at the top (with the existing ones):

```python
import logging

from sdfb_core.observability import log_milestone
```

Add near the other module constants:

```python
# Control-character guard (WS1 §3c): values containing C0/C1 control bytes
# in a STRING column usually mean binary data mis-declared upstream
# (2026-07-20 E2E: COL_048 landed garbled bytes verbatim). Accented /
# non-ASCII text is NOT flagged — only control ranges.
_NONPRINTABLE_RATIO_THRESHOLD = 0.05
```

Add the helper (below `_column_values`):

```python
def _has_control_chars(s: str) -> bool:
    return any(
        (ord(ch) < 32 and ch not in "\t\n\r") or 127 <= ord(ch) <= 159 for ch in s
    )


def _warn_if_nonprintable(field: FieldSchema, non_null: list[object]) -> None:
    if field.bq_type not in {"STRING", "BYTES"} or not non_null:
        return
    strs = [str(v) for v in non_null]
    ratio = sum(1 for s in strs if _has_control_chars(s)) / len(strs)
    if ratio >= _NONPRINTABLE_RATIO_THRESHOLD:
        log_milestone(
            "column_nonprintable",
            level=logging.WARNING,
            column=field.name,
            ratio=round(ratio, 3),
        )
```

In `profile_column`, call it right after `non_null = _non_null(raw)` (before the empty-check):

```python
    _warn_if_nonprintable(field, non_null)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_b2_temporal.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check packages/sdfb-core packages/sdfb-tests
git add -u
git commit -m "feat(b2): column_nonprintable milestone for BYTES-as-STRING columns (WS1 §3c)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Full-baseline verification

**Files:**
- No new files; fixes only if the baseline finds regressions.

- [ ] **Step 1: Run the full laptop baseline**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests -m "not gpu and not gcp" -q`
Expected: everything green (382 baseline + the ~13 new tests). If an existing test fails, fix forward within this task — the likeliest candidates are DoFn tests that asserted the old eager `client_setup` order, or profile tests whose fixtures accidentally exceed the new caps.

- [ ] **Step 2: Lint the whole repo**

Run: `uv run ruff check .`
Expected: clean.

- [ ] **Step 3: Commit any regression fixes**

```bash
git add -u
git commit -m "test: WS1 baseline regression fixes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(Skip the commit if Step 1–2 needed no changes.)

**M4 follow-up (out of laptop scope, tracked for the run matrix):** re-run the b2 tier per `docs/RUN_PLAYBOOK.md`; acceptance is `memorization_flags=[]` in the new run's `gcp_metrics.json`, no `model_client_setup_start` milestone on a run whose columns all resolve without the LLM, and temporal columns' `copy_ratio` at or below the b1 2026-07-19 levels (0.007–0.156).
