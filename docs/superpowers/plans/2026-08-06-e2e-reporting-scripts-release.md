# E2E reporting, script scopes & post-merge release layer — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved spec `docs/superpowers/specs/2026-08-06-e2e-reporting-scripts-release-design.md`: scripts/<scope>/ convention (Phase A), BQ-direct E2E samples + chained crosscheck + stats diff (Phase B), post-merge SemVer tag + deterministic release report (Phase C).

**Architecture:** Scripts stay self-contained CLI files under `scripts/<scope>/`, unit-tested via the repo's importlib idiom (see `packages/sdfb-tests/tests/unit/scripts/test_e2e_bundle_export.py:1-32`). Pure logic (SQL builders, diff math, bump parsing, delta computation) lives in module-level functions so tests never touch GCP. The release layer works exclusively off committed `integration_test/<JOB_ID>/` artifacts read via `git show`, so the GH Action needs no GCP credentials.

**Tech Stack:** Python 3.11, `google-cloud-bigquery` (ADC), `sdfb_core.stats.source_stats.profile_source_table` (ADR 0022 profiler), matplotlib (Agg), pytest fixtures, GitHub Actions + uv.

## Global Constraints

- No Vertex AI / external LLM APIs / Dataplex / Looker anywhere (CLAUDE.md hard constraints).
- All new tests run in the default `pytest -m "not gpu and not gcp"` set; anything touching a live BQ client is behind `main()`/CLI, never in the pure functions tests cover.
- Scripts are tested by loading them with `importlib.util.spec_from_file_location` from `packages/sdfb-tests/tests/unit/scripts/` — path root is `Path(__file__).parents[5]`.
- New scripts NEVER land at `scripts/` root — only under `scripts/<scope>/`.
- Reports persisted to the repo carry `table_1`/`col_1`-style tokens only; the leak scan is a hard gate (non-zero exit) before anything is written for commit.
- Conventional-commit messages; every task ends with its own commit; end commit messages with the Claude co-author line.
- Verification commands: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q`, `uv run ruff check .`.
- CI runners set `UV_DEFAULT_INDEX: https://pypi.org/simple` (corp JFrog index unreachable there — see `.github/workflows/ci.yml`).

---

## Phase A — `scripts/doc/` + convention

### Task 1: Move figure scripts to `scripts/doc/`, fix references, document the convention

**Files:**
- Create: `scripts/doc/` (via `git mv` of the 4 figure scripts), `scripts/README.md`
- Modify: `scripts/doc/make_eval_figures.py`, `scripts/doc/make_source_stats_figures.py`, `scripts/doc/make_ws5_figures.py`, `scripts/doc/make_ws6_figures.py` (asset-path depth), `CLAUDE.md`, `.claude/skills/visual-first-documentation/SKILL.md`, `docs/adr/0022-stats-driven-generation.md`, `docs/designs/2026-08-05-source-table-stats.md`, `docs/designs/2026-07-27-ws6-pipeline-shape.md`, `docs/designs/2026-07-26-ws5-generation-throughput.md`, `docs/designs/2026-07-07-evaluation-framework-design.md`, `docs/superpowers/plans/2026-07-26-ws5-generation-throughput.md`
- Test: none (doc/infra task — verification is grep + regeneration run)

**Interfaces:**
- Produces: canonical scoped paths `scripts/doc/make_<topic>_figures.py` that every later task and doc references.

- [ ] **Step 1: Move the scripts**

```bash
mkdir -p scripts/doc
git mv scripts/make_eval_figures.py scripts/make_source_stats_figures.py \
       scripts/make_ws5_figures.py scripts/make_ws6_figures.py scripts/doc/
```

- [ ] **Step 2: Fix the asset-path depth (CRITICAL — the scripts resolve assets relative to their own file)**

`scripts/doc/make_source_stats_figures.py:39` is
`ASSETS = Path(__file__).resolve().parents[1] / "docs" / "designs" / "assets"`.
One directory deeper means `parents[1]` → `parents[2]`. Find every occurrence
in all four scripts and bump the index:

```bash
grep -n "parents\[1\]" scripts/doc/*.py
```

Edit each hit to `parents[2]`. If any script uses a different root idiom
(e.g. `parents[0]`, cwd-relative), adjust it to `parents[2]`-from-`__file__`
so invocation location stops mattering.

- [ ] **Step 3: Verify by regenerating one figure set**

```bash
uv run --no-sync python3 scripts/doc/make_source_stats_figures.py
git status --short docs/designs/assets/   # regenerated PNGs may show as modified — that's fine (deterministic seeds should produce no diff; investigate any diff)
```

Expected: script runs green, prints its palette-separation check.

- [ ] **Step 4: Update every reference to the old paths**

```bash
grep -rn "scripts/make_" --include='*.md' docs .claude CLAUDE.md
```

Known hits to update (`scripts/make_X.py` → `scripts/doc/make_X.py`):
`.claude/skills/visual-first-documentation/SKILL.md` (quick-reference command),
`docs/adr/0022-stats-driven-generation.md`,
`docs/designs/2026-08-05-source-table-stats.md`,
`docs/designs/2026-07-27-ws6-pipeline-shape.md`,
`docs/designs/2026-07-26-ws5-generation-throughput.md`,
`docs/designs/2026-07-07-evaluation-framework-design.md`,
`docs/superpowers/plans/2026-07-26-ws5-generation-throughput.md`.
Also fix the intra-script comment reference in
`scripts/doc/make_source_stats_figures.py:20` (`scripts/make_ws6_figures.py` →
`scripts/doc/make_ws6_figures.py`).

- [ ] **Step 5: Write `scripts/README.md`**

```markdown
# scripts/ — scoped script layout

Every script lives under `scripts/<scope>/`. New scripts NEVER land at the
root. Current scopes:

| Scope | Owns |
|---|---|
| `doc/` | figure/report generators for documentation (`make_<topic>_figures.py`; see `.claude/skills/visual-first-documentation/SKILL.md`) |
| `e2e/` | integration-test analysis, GCP probes, sample fetch, crosschecks, redaction |
| `release/` | post-merge versioning + release reports (see `.github/workflows/release_tag_report.yaml`) |

Future scopes (`ddl/`, `gcp/`, `automation/`, `config/`, …) are created on
first need. Root-level scripts (`extract_ddl.py`, `deployment_prerequisites.py`,
`derive_landing_schema.py`, `probe_gpu_dataflow.sh`, `hello_synthetic_mlx.py`,
`run_tableset.py`) are grandfathered and move to a scope when next touched.

Docs, prompts, agents and skills reference scripts by their scoped path.
Tests load scripts via importlib from `packages/sdfb-tests/tests/unit/scripts/`.
```

- [ ] **Step 6: Add the convention pointer to CLAUDE.md**

In the `## Entry points` section, append one line:

```markdown
- Scripts follow a `scripts/<scope>/` layout (`doc/`, `e2e/`, `release/`) — convention in [`scripts/README.md`](scripts/README.md); new scripts never land at the root.
```

- [ ] **Step 7: Acceptance grep + commit**

```bash
grep -rn "scripts/make_" --include='*.md' docs/adr docs/designs .claude CLAUDE.md
# Expected: 0 hits (docs/superpowers/specs records the move — that's the only allowed remainder)
uv run ruff check scripts/doc/
git add -A && git commit -m "refactor(scripts): scoped scripts/<scope>/ layout, figure scripts under scripts/doc/"
```

---

## Phase B — BQ-direct samples + stats diff + prompt chaining

### Task 2: `scripts/e2e/e2e_fetch_samples.py`

**Files:**
- Create: `scripts/e2e/e2e_fetch_samples.py`
- Test: `packages/sdfb-tests/tests/unit/scripts/test_e2e_fetch_samples.py`

**Interfaces:**
- Consumes: nothing from other tasks (self-contained; copies the small ADC/client helpers, same pattern as `e2e_gcp_probe.py::preflight_adc`/`_bq_client`).
- Produces: CLI `python scripts/e2e/e2e_fetch_samples.py --landing-fqn <fqn> --project <p> --job-id <id> [--engine-label label=run_id …] [--rows N] [--run-id-col col] [--out-dir integration_test]`; pure functions `build_sample_sql(fqn: str, *, rows: int, run_id_col: str | None, run_id: str | None) -> str` and `write_csv(rows: list[dict], columns: list[str], path: Path) -> int` (returns row count written). Output files: `integration_test/<JOB_ID>/<engine>_sample.csv`.

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for scripts/e2e/e2e_fetch_samples.py (importlib idiom)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "e2e_fetch_samples.py"
_spec = importlib.util.spec_from_file_location("e2e_fetch_samples", _SCRIPT)
fetch = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fetch
_spec.loader.exec_module(fetch)


def test_sql_is_deterministic_hash_ordered():
    sql = fetch.build_sample_sql("p.d.t", rows=100, run_id_col=None, run_id=None)
    assert "FARM_FINGERPRINT(TO_JSON_STRING(t))" in sql
    assert "ORDER BY" in sql and "LIMIT 100" in sql
    assert "RAND()" not in sql


def test_sql_filters_run_id():
    sql = fetch.build_sample_sql("p.d.t", rows=5, run_id_col="run_id", run_id="r-1")
    assert "run_id = @run_id" in sql  # parameterized, never inlined


def test_write_csv_orders_columns_and_counts(tmp_path):
    out = tmp_path / "b1_rag_sample.csv"
    n = fetch.write_csv([{"b": 2, "a": 1}], ["a", "b"], out)
    assert n == 1
    assert out.read_text().splitlines()[0] == "a,b"


def test_write_csv_refuses_empty(tmp_path):
    out = tmp_path / "x.csv"
    with pytest.raises(SystemExit):
        fetch.write_csv([], ["a"], out)
    assert not out.exists()
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/scripts/test_e2e_fetch_samples.py -q
```
Expected: FAIL (file not found / attribute errors).

- [ ] **Step 3: Implement**

Self-contained script, module docstring naming the spec. Key parts:

```python
def build_sample_sql(fqn, *, rows, run_id_col, run_id):
    where = f"WHERE {run_id_col} = @run_id" if run_id_col and run_id else ""
    return (
        f"SELECT * FROM `{fqn}` t {where} "
        "ORDER BY FARM_FINGERPRINT(TO_JSON_STRING(t)) "
        f"LIMIT {int(rows)}"
    )

def write_csv(rows, columns, path):
    if not rows:
        sys.stderr.write(f"no rows fetched for {path.name} - refusing to write an empty CSV\n")
        raise SystemExit(2)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)
```

`main(argv)`: argparse per the Interfaces block; ADC preflight + BigQuery
client + `x-goog-user-project` copied from `e2e_gcp_probe.py:82-127` (small,
deliberate duplication — scripts stay standalone); column order from the
query result schema; JSON-encode nested values; `--engine-label` repeatable
(`label=run_id`), default single fetch labeled `sample`; run-id parameter via
`bigquery.ScalarQueryParameter("run_id", "STRING", run_id)`. Deviation from
spec noted here deliberately: hash-`ORDER BY` replaces the `MOD(...)` filter —
same determinism guarantee, no tuning constant.

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/scripts/test_e2e_fetch_samples.py -q
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/e2e/e2e_fetch_samples.py packages/sdfb-tests/tests/unit/scripts/test_e2e_fetch_samples.py
git commit -m "feat(e2e): BQ-direct deterministic sample fetch (no manual CSV export)"
```

### Task 3: `scripts/e2e/source_synthetic_stats_diff.py`

**Files:**
- Create: `scripts/e2e/source_synthetic_stats_diff.py`
- Test: `packages/sdfb-tests/tests/unit/scripts/test_source_synthetic_stats_diff.py`

**Interfaces:**
- Consumes: `sdfb_core.stats.source_stats.profile_source_table(table_schema, reference_rows, contract=None)` and `sdfb_core.contracts.schema.TableSchema` / `TableInfo` / `FieldSchema` (construct as `TableSchema(table_info=TableInfo(table_id=...), columns=[FieldSchema(name=..., bq_type=<data_type>, mode="NULLABLE"), ...])` — implementer: confirm `FieldSchema` field names at `packages/sdfb-core/src/sdfb_core/contracts/schema.py` before coding). Fetch mechanics duplicated from Task 2's pattern.
- Produces: CLI `--source-fqn --synthetic-fqn --project [--rows 10000] --out-json <path> [--out-md <path>]`; pure functions `diff_profiles(src: dict[str, dict], syn: dict[str, dict]) -> dict`, `decile_ks(a: list[float], b: list[float]) -> float`, `length_band(strings: list[str]) -> dict` (p05/p50/p95), `render_md(diff: dict) -> str`. JSON shape: `{"columns": {name: {...}}, "table": {...}, "evaluation": None}` — the `evaluation` key is the reserved ws3 slot. Default out path when chained from E2E: `integration_test/<JOB_ID>/stats_diff.json`.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "source_synthetic_stats_diff.py"
_spec = importlib.util.spec_from_file_location("source_synthetic_stats_diff", _SCRIPT)
sd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sd
_spec.loader.exec_module(sd)


def _profile(rows, cols):
    from sdfb_core.contracts.schema import FieldSchema, TableInfo, TableSchema
    schema = TableSchema(
        table_info=TableInfo(table_id="t"),
        columns=[FieldSchema(name=c, bq_type="STRING", mode="NULLABLE") for c in cols],
    )
    from sdfb_core.stats.source_stats import profile_source_table
    return profile_source_table(schema, rows)


def test_entropy_gap_detects_collapse():
    src = _profile([{"c": v} for v in "abcd" * 25], ["c"])       # balanced, 4 values
    syn = _profile([{"c": "a"} for _ in range(100)], ["c"])      # collapsed
    diff = sd.diff_profiles(src, syn)
    assert diff["columns"]["c"]["entropy_gap"] > 0.5


def test_decile_ks_zero_for_identical():
    assert sd.decile_ks([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 0.0


def test_decile_ks_positive_for_shifted():
    a = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    b = [x + 5 for x in a]
    assert sd.decile_ks(a, b) > 0.3


def test_diff_reserves_evaluation_slot_and_renders():
    src = _profile([{"c": "x"}], ["c"])
    diff = sd.diff_profiles(src, src)
    assert diff["evaluation"] is None
    md = sd.render_md(diff)
    assert "entropy" in md.lower()
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/scripts/test_source_synthetic_stats_diff.py -q
```
Expected: FAIL.

- [ ] **Step 3: Implement**

- `decile_ks(a, b)`: both are 11-point quantile vectors; evaluate the two
  piecewise-linear empirical CDFs on the union grid of both vectors via
  `numpy.interp(grid, vec, [0, .1, …, 1])` and return
  `float(max(abs(F_a - F_b)))`.
- `diff_profiles`: per common column, read the profiler entry keys (entropy
  norm, top1 share, deciles, null/empty fractions — use the exact key names
  `profile_source_table` emits; implementer greps
  `source_stats.py::_add_value_mix/_add_numeric/_add_temporal` for them) and
  emit: `entropy_gap` (src − syn, normalized), `top1_delta`,
  `decile_ks` (when both sides carry deciles), `null_delta`, `empty_delta`,
  `length_band` src/syn + drift for STRING columns (computed by this script
  from the sampled rows — the profiler does not own length bands),
  `verdict` in {"ok", "warn", "fail"} via thresholds
  (entropy_gap > 0.3 or decile_ks > 0.2 or abs parity delta > 0.1 → "warn";
  double those → "fail").
- `render_md(diff)`: ranked table (worst first) + one bullet per non-"ok"
  column mapping the signal to a generation code area (entropy gap → mode
  collapse in pool/sampling; decile_ks → inverse-CDF sampler; parity → the
  empty-parity seam).
- `main(argv)`: fetch bounded samples of both tables (Task 2 pattern:
  hash-ordered LIMIT), build `TableSchema` from the query result schema,
  profile both with `profile_source_table(schema, rows)`, diff, write JSON
  (+ optional md). Column sets are intersected; skipped columns listed in
  `diff["table"]["skipped"]`, never dropped silently.

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/scripts/test_source_synthetic_stats_diff.py -q
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/e2e/source_synthetic_stats_diff.py packages/sdfb-tests/tests/unit/scripts/test_source_synthetic_stats_diff.py
git commit -m "feat(e2e): source-vs-synthetic stats diff reusing the ADR 0022 profiler"
```

### Task 4: Prompt updates (E2E chains fetch + crosscheck + stats diff)

**Files:**
- Modify: `.github/prompts/end_to_end_validation_report_generation.prompt.md`, `.github/prompts/freetext_crosscheck_report_generation.prompt.md`

**Interfaces:**
- Consumes: Task 2 + Task 3 CLIs exactly as their Interfaces blocks define.

- [ ] **Step 1: Edit the E2E prompt**

1. Inputs table, `CSVS` row — append: *"optional: when omitted (or a file is
   missing), Step 1.5 fetches the samples from `LANDING_FQN` via ADC."*
2. New **Step 1.5** after Step 1:

```markdown
## Step 1.5 — Materialize missing sample CSVs from BigQuery (no manual export)

For every engine whose CSV under `integration_test/<JOB_ID>/` is missing:

​```bash
python scripts/e2e/e2e_fetch_samples.py \
  --project <PROJECT> --landing-fqn <LANDING_FQN> --job-id <JOB_ID> \
  $(for e in <ENGINE_LABEL=RUN_ID>; do echo --engine-label $e; done) \
  --rows 10000
​```

Deterministic (hash-ordered) — re-runs fetch the same rows. Only stop if the
fetch itself fails; never hand-copy CSVs again.
```

3. New **Step 3.5** (mandatory) after Step 3:

```markdown
## Step 3.5 — Free-text crosscheck + source-vs-synthetic stats diff (mandatory)

Chain the free-text pattern crosscheck (its own prompt:
`freetext_crosscheck_report_generation.prompt.md`) and the stats diff — both
write into the per-deployment folder when run from E2E:

​```bash
python scripts/e2e/freetext_crosscheck.py \
  --source-fqn <SOURCE_FQN> --synthetic-fqn <LANDING_FQN> \
  --columns "<FREETEXT_COLS>" \
  --out-json integration_test/<JOB_ID>/freetext_crosscheck_metrics.json \
  --out-md   integration_test/<JOB_ID>/freetext_crosscheck_report.md

python scripts/e2e/source_synthetic_stats_diff.py \
  --source-fqn <SOURCE_FQN> --synthetic-fqn <LANDING_FQN> --project <PROJECT> \
  --out-json integration_test/<JOB_ID>/stats_diff.json \
  --out-md   integration_test/<JOB_ID>/stats_diff.md
​```

`FREETEXT_COLS` defaults to the free-text subset discovered in Steps 2–3.
```

4. Step 5 report sections: insert **"3.5 Source vs synthetic statistics"**
   (stats-diff + crosscheck headline numbers, each finding traced to a
   generation code area, feeding the backlog).
5. Step 6: add both new JSONs + the crosscheck md to the `--metrics`/bundle
   inputs. Step 7 checklist: add "crosscheck + stats-diff artifacts present
   in `integration_test/<JOB_ID>/`".
6. Add `FREETEXT_COLS` to the Inputs table (optional, discovered if absent).

- [ ] **Step 2: Edit the crosscheck prompt**

In the header + Step 2: note the E2E prompt chains this prompt and overrides
`--out-json/--out-md` into `integration_test/<JOB_ID>/`; standalone default
stays `output/freetext_crosscheck/`.

- [ ] **Step 3: Verify + commit**

```bash
grep -n "Step 1.5\|Step 3.5\|e2e_fetch_samples\|source_synthetic_stats_diff" \
  .github/prompts/end_to_end_validation_report_generation.prompt.md
git add .github/prompts && git commit -m "docs(prompts): E2E report fetches samples from BQ and chains crosscheck + stats diff"
```

---

## Phase C — post-merge release layer

### Task 5: Extract `scripts/e2e/redaction.py` from the bundle exporter

**Files:**
- Create: `scripts/e2e/redaction.py`
- Modify: `scripts/e2e/e2e_bundle_export.py`
- Test: `packages/sdfb-tests/tests/unit/scripts/test_redaction.py` (new, minimal) + existing `test_e2e_bundle_export.py` must stay green untouched

**Interfaces:**
- Produces: importable module with `Mapping` (dataclass, unchanged fields), `build_mapping(gcp, offline, bq=None) -> Mapping`, `register_csv(m, text, *, redact_values)`, `redact_csv(m, text) -> str`, `redact_text(m, text) -> str` (identifier/value token replacement over arbitrary text — new, needed by Task 6), `leak_scan(root: Path, m: Mapping) -> list[tuple[str, str]]`. Loader idiom for sibling scripts: `sys.path.insert(0, str(Path(__file__).resolve().parent))` then `import redaction`.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "redaction.py"
_spec = importlib.util.spec_from_file_location("redaction", _SCRIPT)
red = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = red
_spec.loader.exec_module(red)


def test_redact_text_replaces_known_identifiers():
    m = red.Mapping()
    m.identifiers["real-project-id"] = "PROJECT_1"
    out = red.redact_text(m, "job ran in real-project-id today")
    assert "real-project-id" not in out and "PROJECT_1" in out


def test_leak_scan_flags_surviving_token(tmp_path):
    m = red.Mapping()
    m.identifiers["secret-dataset"] = "DATASET_1"
    (tmp_path / "r.md").write_text("still says secret-dataset")
    hits = red.leak_scan(tmp_path, m)
    assert hits
```

(Adjust `Mapping()` construction to its real dataclass signature at
`e2e_bundle_export.py:75` when extracting — the test must build the smallest
valid instance.)

- [ ] **Step 2: Run to verify failure** — `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/scripts/test_redaction.py -q` → FAIL (no file).

- [ ] **Step 3: Extract**

Move from `e2e_bundle_export.py` into `redaction.py`: the regex/constants
block (`_VALUE_LEAF_KEYS` … `_MIN_TEXT_VALUE_LEN`), `Mapping`, `_split_fqn`,
`build_mapping`, `_collect_identifiers`, `_collect_columns`,
`_collect_values`, `_register_csv` → `register_csv`, `_redact_csv` →
`redact_csv`, `_leak_scan` → `leak_scan`. Add `redact_text(m, text)`
(longest-token-first replacement of every mapping key in `text`, reusing the
same email regex fallback). In `e2e_bundle_export.py`: sibling import (see
Interfaces) + thin aliases so the existing test file keeps passing unchanged
(`_leak_scan = redaction.leak_scan`, `_collect_identifiers =
redaction._collect_identifiers`, `Mapping = redaction.Mapping`, etc. — check
`test_e2e_bundle_export.py` for the exact names it touches).

- [ ] **Step 4: Run both test files**

```bash
uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/scripts/test_redaction.py \
  packages/sdfb-tests/tests/unit/scripts/test_e2e_bundle_export.py -q
```
Expected: all pass, bundle-export tests unmodified.

- [ ] **Step 5: Commit**

```bash
git add scripts/e2e/redaction.py scripts/e2e/e2e_bundle_export.py packages/sdfb-tests/tests/unit/scripts/test_redaction.py
git commit -m "refactor(e2e): extract shared redaction + leak-scan into scripts/e2e/redaction.py"
```

### Task 6: Release logic — bump parsing, artifact discovery, deltas

**Files:**
- Create: `scripts/release/__init__.py` (empty), `scripts/release/make_release_report.py` (logic half)
- Test: `packages/sdfb-tests/tests/unit/scripts/test_make_release_report.py`

**Interfaces:**
- Produces: `parse_bump(title: str) -> str` ("major"|"minor"|"patch"), `next_version(prev: str | None, bump: str) -> str` (prev like "v0.3.1" or None → "v0.1.0"; pre-1.0 demotes major→minor), `discover_artifact_sets(ref: str) -> dict[str, dict]` (job_id → {"gcp":…, "offline":…, "crosscheck":…, "stats_diff":…} parsed from `git show <ref>:integration_test/<job>/<file>.json`; missing files → key absent), `latest_job(sets: dict) -> str | None` (lexicographically greatest job id — ids start with a timestamp), `compute_deltas(base: dict | None, head: dict | None) -> dict` (pure; sections `performance`, `vllm`, `quality`, each metric `{"base": x, "head": y, "delta": y-x}` with `None` for unmeasured — tokens/s only when milestone token counts exist; explicit `"missing"` list when either side has no artifacts).

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[5] / "scripts" / "release" / "make_release_report.py"
_spec = importlib.util.spec_from_file_location("make_release_report", _SCRIPT)
rel = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rel
_spec.loader.exec_module(rel)


import pytest


@pytest.mark.parametrize(
    ("title", "bump"),
    [
        ("feat(stats): add tiers", "minor"),
        ("fix(b1): pool race", "patch"),
        ("perf(vllm): batch size", "patch"),
        ("docs(adr): new decision", "patch"),
        ("chore!: drop py310", "major"),
        ("feat(api)!: new contract", "major"),
        ("not conventional at all", "patch"),
    ],
)
def test_parse_bump(title, bump):
    assert rel.parse_bump(title) == bump


def test_next_version_first_tag():
    assert rel.next_version(None, "minor") == "v0.1.0"


def test_next_version_pre_1_0_demotes_major():
    assert rel.next_version("v0.3.1", "major") == "v0.4.0"


def test_next_version_bumps():
    assert rel.next_version("v0.3.1", "minor") == "v0.4.0"
    assert rel.next_version("v0.3.1", "patch") == "v0.3.2"
    assert rel.next_version("v1.2.3", "major") == "v2.0.0"


def test_compute_deltas_marks_missing_side():
    d = rel.compute_deltas(None, {"gcp": {"jobs": []}})
    assert "base" in d["missing"]


def test_compute_deltas_diffs_wall_clock():
    base = {"gcp": {"jobs": [{"execution_seconds": 100.0}]}}
    head = {"gcp": {"jobs": [{"execution_seconds": 80.0}]}}
    d = rel.compute_deltas(base, head)
    assert d["performance"]["execution_seconds"]["delta"] == -20.0
```

- [ ] **Step 2: Run to verify failure** — same pytest command, expect FAIL.

- [ ] **Step 3: Implement the logic half**

```python
_TITLE_RE = re.compile(r"^(?P<type>[a-z]+)(\([^)]*\))?(?P<bang>!)?:")

def parse_bump(title):
    m = _TITLE_RE.match(title.strip())
    if not m:
        return "patch"
    if m.group("bang") or "BREAKING CHANGE" in title:
        return "major"
    return "minor" if m.group("type") == "feat" else "patch"

def next_version(prev, bump):
    if prev is None:
        return "v0.1.0"
    major, minor, patch = (int(x) for x in prev.lstrip("v").split("."))
    if major == 0 and bump == "major":
        bump = "minor"
    if bump == "major":
        return f"v{major + 1}.0.0"
    if bump == "minor":
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"
```

`discover_artifact_sets(ref)`: `git ls-tree -r --name-only <ref> --
integration_test` (subprocess, `check=True`), group paths by
`integration_test/<job_id>/`, load the four known JSON basenames
(`e2e_gcp_metrics.json`, `e2e_validation_metrics.json`,
`freetext_crosscheck_metrics.json`, `stats_diff.json`) via
`git show <ref>:<path>`; tolerate absent files, never absent-then-crash.
`compute_deltas`: walk a fixed metric map — performance
(`execution_seconds`, per-phase durations, custom counters), vllm (milestone
durations; tokens/s = tokens/duration only when both present else `None`),
quality (dup ratio, top-value share max, shape_recall/precision,
copy_fraction, entropy-gap max, decile_ks max). Every metric read defensively
(`dict.get` chains) — artifact schemas predate this script.

- [ ] **Step 4: Run tests** — expect all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/release packages/sdfb-tests/tests/unit/scripts/test_make_release_report.py
git commit -m "feat(release): bump parsing, artifact discovery and metric deltas"
```

### Task 7: Release report rendering — charts, redaction gate, CLI

**Files:**
- Modify: `scripts/release/make_release_report.py` (rendering half)
- Modify: `pyproject.toml` (add `matplotlib>=3.9` to the dev dependency group — figure scripts have relied on an ad-hoc local install until now; CI needs it declared)
- Test: extend `packages/sdfb-tests/tests/unit/scripts/test_make_release_report.py`

**Interfaces:**
- Consumes: Task 6 functions; Task 5 `redaction` module (sibling-import from `scripts/e2e/` via `sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))`).
- Produces: `render_report(version, change_log: list[dict], deltas: dict, history: list[tuple[str, dict]]) -> str`; `make_charts(deltas, history, assets_dir: Path) -> list[Path]` (bar `step_time_before_after.png`, line `metric_evolution.png`); `main(argv)` with `--base-ref --head-ref --version --out-dir docs/releases [--dry-run]` plus a helper mode `--print-next-version --title "<merge title>" [--prev <tag>]` that prints the computed next tag and exits (the Action uses it — no inline Python in YAML); writes `docs/releases/<version>/report.md` + `assets/`, regenerates `docs/releases/README.md` index; **redaction gate**: builds a `Mapping` from the head artifact JSONs, `redact_text`s the report, `leak_scan`s the out dir, exits 3 on hits.

- [ ] **Step 1: Write the failing tests (append to the Task 6 test file)**

```python
def _fixture_deltas():
    return {
        "performance": {"execution_seconds": {"base": 100.0, "head": 80.0, "delta": -20.0}},
        "vllm": {"tokens_per_s": {"base": None, "head": None, "delta": None}},
        "quality": {"copy_fraction_max": {"base": 0.1, "head": 0.05, "delta": -0.05}},
        "missing": [],
        "base_job": "j1", "head_job": "j2",
    }


def test_render_report_has_sections_and_no_placeholders():
    md = rel.render_report("v0.2.0", [{"type": "feat", "title": "feat: x"}],
                           _fixture_deltas(), history=[("v0.1.0", _fixture_deltas())])
    for section in ("# Release v0.2.0", "Change summary", "Before / after", "Charts"):
        assert section in md


def test_render_report_states_missing_runs():
    d = _fixture_deltas() | {"missing": ["base"]}
    md = rel.render_report("v0.2.0", [], d, history=[])
    assert "no integration run" in md.lower()


def test_make_charts_writes_pngs(tmp_path):
    paths = rel.make_charts(_fixture_deltas(), [("v0.1.0", _fixture_deltas())], tmp_path)
    assert paths and all(p.exists() and p.suffix == ".png" for p in paths)


def test_print_next_version_mode(capsys):
    rc = rel.main(["--print-next-version", "--title", "feat: x", "--prev", "v0.1.0"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "v0.2.0"
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

- `matplotlib.use("Agg")` before pyplot import. **Load the `dataviz` skill
  before writing the chart code** — palette/marks/axis rules come from it.
- `render_report`: header (version, date from `--date` arg — never
  `datetime.now()` hidden inside, the Action passes it), change summary
  grouped by conventional type, before/after tables from `deltas` (a missing
  side renders the explicit sentence "no integration run landed for this
  release"), chart image links, footer naming generator + refs.
- `main(argv)`: resolve base ref (`git describe --tags --abbrev=0 HEAD^`
  fallback None), head `HEAD`; version from `--version` (Action computes it)
  or `next_version(prev, parse_bump(head_title))`; discovery + deltas +
  history over all `v*` tags; charts; redaction gate; index regeneration
  (table row per release dir: tag · date · headline delta); `--dry-run`
  prints version + would-write paths, writes nothing, exits 0.

- [ ] **Step 4: Run the full script-test file + ruff** — expect green.

- [ ] **Step 5: Local end-to-end dry-run**

```bash
uv sync --group dev
uv run --no-sync python3 scripts/release/make_release_report.py --dry-run
```
Expected: prints computed version (v0.1.0 — no tags exist) + target paths.

- [ ] **Step 6: Commit**

```bash
git add scripts/release/make_release_report.py pyproject.toml uv.lock packages/sdfb-tests/tests/unit/scripts/test_make_release_report.py
git commit -m "feat(release): report rendering, evolution charts and redaction gate"
```

### Task 8: GH Action `release_tag_report.yaml`

**Files:**
- Create: `.github/workflows/release_tag_report.yaml`

**Interfaces:**
- Consumes: Task 7 CLI exactly (`--version`, `--date`, default refs).

- [ ] **Step 1: Write the workflow**

```yaml
name: Release tag + report

# Post-merge release layer (spec: docs/superpowers/specs/
# 2026-08-06-e2e-reporting-scripts-release-design.md). Tags every merge to
# master with SemVer derived from the squash-merge title, generates the
# redacted before/after release report from committed integration_test/
# artifacts (NO GCP access), and commits docs/releases/<version>/.

on:
  push:
    branches: [master]
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: release-${{ github.ref }}
  cancel-in-progress: false

jobs:
  tag-and-report:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    # Self-trigger guard: the report commit below carries this marker.
    if: ${{ !contains(github.event.head_commit.message, '[release-report]') }}
    env:
      UV_DEFAULT_INDEX: https://pypi.org/simple
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0          # full history: tags + git show at base ref
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --group dev
      - name: Compute version
        id: ver
        run: |
          PREV=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
          TITLE=$(git log -1 --pretty=%s)
          VERSION=$(uv run --no-sync python3 scripts/release/make_release_report.py \
            --print-next-version --title "$TITLE" ${PREV:+--prev "$PREV"})
          echo "version=$VERSION" >> "$GITHUB_OUTPUT"
          echo "prev=$PREV" >> "$GITHUB_OUTPUT"
      - name: Generate report
        run: |
          PREV="${{ steps.ver.outputs.prev }}"
          uv run --no-sync python3 scripts/release/make_release_report.py \
            --version "${{ steps.ver.outputs.version }}" \
            --date "$(date -u +%Y-%m-%d)" \
            ${PREV:+--base-ref "$PREV"} \
            --head-ref "${{ github.sha }}" \
            --out-dir docs/releases
      - name: Tag, commit report, push
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/releases
          git commit -m "docs(releases): ${{ steps.ver.outputs.version }} report [release-report]"
          git tag "${{ steps.ver.outputs.version }}"
          git push origin master --tags
```

- [ ] **Step 2: Verify locally what CI will run**

```bash
uv run --no-sync python3 scripts/release/make_release_report.py --dry-run
uv run --no-sync python3 -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/release_tag_report.yaml').read_text()); print('yaml ok')"
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release_tag_report.yaml
git commit -m "ci(release): tag every master merge and commit the redacted release report"
```

### Task 9: PR template, release-report skill, releases index seed

**Files:**
- Create: `.github/PULL_REQUEST_TEMPLATE.md`, `.claude/skills/release-report/SKILL.md`, `docs/releases/README.md`

**Interfaces:**
- Consumes: Task 6 bump semantics; Task 7 report layout.

- [ ] **Step 1: `.github/PULL_REQUEST_TEMPLATE.md`**

```markdown
## What & why

<!-- 2-3 sentences; link the ADR/design doc if one exists -->

## Release checklist (drives the post-merge automation)

- [ ] Title is a conventional commit (`feat:`/`fix:`/`perf:`/`docs:`/`chore:`…) —
      it decides the SemVer bump (feat → minor, fix/perf → patch, `!` → major).
      Expected bump: `v_._._`
- [ ] Integration-test artifacts for runs this PR relies on are committed under
      `integration_test/<JOB_ID>/` and the JOB_IDs are listed here: `…`
- [ ] `oss/` bundle leak scan is clean for any newly committed artifacts
- [ ] Docs/ADRs updated (or explicitly not needed)

<!-- After merge: the release Action tags vX.Y.Z and commits
     docs/releases/vX.Y.Z/report.md. Run the release-report skill for the
     insights pass. -->
```

- [ ] **Step 2: `.claude/skills/release-report/SKILL.md`**

```markdown
---
name: release-report
description: Use after a PR merges to master (or when the release Action's deterministic report lands) to write the insights section — regression calls, bottleneck attribution, optimization candidates — on top of docs/releases/<version>/report.md.
---

# Release report — interpretation layer

The GH Action (`.github/workflows/release_tag_report.yaml`) already produced
the deterministic numbers via `scripts/release/make_release_report.py`.
This skill adds judgment. NEVER edit the generated numbers or charts.

1. Read `docs/releases/<version>/report.md` + the underlying
   `integration_test/<JOB_ID>/*.json` for both sides of the diff.
2. Append an `## Insights` section: regressions (call severity), bottleneck
   attribution (which Dataflow step/phase moved and which commit plausibly
   moved it), optimization candidates, follow-up backlog items.
3. Where ADC exists locally, optionally enrich with live Dataflow/BQ detail
   (`scripts/e2e/e2e_gcp_probe.py`) — never required.
4. Redaction rules are identical to the generator's: no FQNs, column names,
   or data values — `table_1`/`col_1` tokens only. Re-run the leak scan after
   editing: `scripts/e2e/redaction.py` gates what may be committed.
5. Commit as `docs(releases): <version> insights`.
```

- [ ] **Step 3: Seed `docs/releases/README.md`**

```markdown
# Release history

Generated by `scripts/release/make_release_report.py` (via
`.github/workflows/release_tag_report.yaml`); this index is regenerated on
every release — do not hand-edit rows.

| Version | Date | Headline delta |
|---|---|---|
```

- [ ] **Step 4: Commit**

```bash
git add .github/PULL_REQUEST_TEMPLATE.md .claude/skills/release-report docs/releases/README.md
git commit -m "docs(release): PR template, release-report skill and releases index"
```

### Task 10: Full verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Full default test suite** — `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q` → all green (845+ pre-existing + ~20 new).
- [ ] **Step 2: Lint + types** — `uv run ruff check .` and `uv run mypy packages/sdfb-core/src` → 0 errors.
- [ ] **Step 3: Acceptance greps**

```bash
grep -rn "scripts/make_" --include='*.md' docs/adr docs/designs .claude CLAUDE.md   # 0 hits
grep -n "Step 3.5" .github/prompts/end_to_end_validation_report_generation.prompt.md # present
uv run --no-sync python3 scripts/release/make_release_report.py --dry-run            # prints v0.1.0 plan
```

- [ ] **Step 4: Figure regeneration spot-check** — `uv run --no-sync python3 scripts/doc/make_ws6_figures.py` → green from the new location.
- [ ] **Step 5: Commit anything the sweep fixed**, message `chore: verification sweep for e2e-reporting/release plan`.

---

## Self-review notes (spec → plan coverage)

- Spec Phase A → Task 1 (moves, `parents[1]` fix, references, README, CLAUDE.md line, acceptance grep).
- Spec B.1 → Task 2 (deviation recorded in-task: hash-`ORDER BY` instead of `MOD`, same determinism).
- Spec B.2 → Task 3 (profiler reuse, KS on decile vectors, parity deltas, length bands computed script-side, `evaluation` slot reserved, skipped columns listed).
- Spec B.3 → Task 4 (Steps 1.5/3.5, report section 3.5, bundle/verify additions, crosscheck prompt note).
- Spec C.1 → Task 8 (bump rules, guard, no-GCP, tag+commit+push).
- Spec C.2 → Tasks 5–7 (redaction extraction shared with bundle exporter; deltas incl. tokens/s honesty guard; bar + evolution charts; leak-scan exit 3; index regeneration; `--dry-run`).
- Spec C.3 → Task 9 PR template. Spec C.4 → Task 9 skill.
- Spec testing section → per-task tests + Task 10 sweep; all laptop-safe.
