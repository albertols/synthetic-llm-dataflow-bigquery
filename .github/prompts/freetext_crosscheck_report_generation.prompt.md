
---
mode: agent
description: >
  Generate a Claude-Code-ready free-text pattern crosscheck report for any
  synthetic-dataflow-bigquery table. Mines the *structural patterns* (shape
  masks, length, character-class profile, sparsity, top literals) of a set of
  free-text / string columns in the LIVE source BigQuery table and measures how
  faithfully the synthetic table reproduced them — surfacing concrete
  synthetic-generation improvements (missing formats, hallucinated formats,
  null/empty-parity gaps, length drift, memorization). Runs standalone or
  chained from the E2E validation report prompt's Step 3.5, which overrides
  `--out-json`/`--out-md` into `integration_test/<JOB_ID>/` — the standalone
  default below is `output/freetext_crosscheck/`. ADC access to the target
  GCP project is a PREREQUISITE and is verified first.
  Inputs: source FQN, synthetic FQN, free-text column list (+ optional sample
  size / top-k). Output:
  output/freetext_crosscheck/report_YYYY_MM_DD_HH_mm.md +
  output/freetext_crosscheck/metrics_YYYY_MM_DD_HH_mm.json.
---

# /freetext_crosscheck_report_generation — Free-Text Pattern Crosscheck Report

## Goal

Produce a **concise, Claude-Code-ready**
`output/freetext_crosscheck/report_YYYY_MM_DD_HH_mm.md` (+ the sibling
`metrics_*.json`) that compares the **structural patterns** of a set of
free-text columns between a live **source** BigQuery table and its **synthetic**
counterpart, and turns every divergence into a prioritized synthetic-generation
improvement the user can paste straight into Claude Code.

This is a **repeated action** run whenever a new synthetic table lands and the
free-text columns look "off". Be deterministic, show real numbers from the
metrics JSON, and keep everything **generic** — no column, dataset, or project
id is hard-coded; they all arrive as inputs.

---

## Inputs (ask the user if not provided; nothing is hard-coded)

| Param | Example | Notes |
|---|---|---|
| `SOURCE_FQN` | `<project>.<dataset>.<TABLE>` | live source table |
| `SYNTHETIC_FQN` | `<project>.synthetic_data.<TABLE>` | synthetic landing table |
| `COLUMNS` | `COL_A,COL_B,COL_C` | comma-separated free-text / STRING columns to crosscheck |
| `SAMPLE_SIZE` | `20000` | rows/table pulled for in-Python shape mining (optional, default 20000) |
| `TOP_K` | `15` | number of top shapes / literals kept per column (optional, default 15) |
| `PROJECT` | `<project>` | optional; defaults to the source project |

If `COLUMNS` is unknown, discover the STRING columns via `INFORMATION_SCHEMA`
(`data_type = 'STRING'`) on the source table and confirm the free-text subset
with the user. Only columns present in **both** tables are analyzed; the rest
are reported as skipped.

**Artifact location**: both outputs live under `output/freetext_crosscheck/`.
The report is `report_YYYY_MM_DD_HH_mm.md`; the metrics JSON that backs every
number is `metrics_YYYY_MM_DD_HH_mm.json` (same timestamp).

---

## Step 0 — PREREQUISITE: verify GCP access (ADC), fail fast otherwise

ADC access to `PROJECT` is **mandatory** — the crosscheck reads two live BQ
tables. Verify first:

```bash
python - <<'PY'
import google.auth, google.auth.transport.requests as t
c,_=google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
c.refresh(t.Request()); print("ADC OK")
PY
```

If this fails, STOP and instruct the user to run (hard requirement):

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project <PROJECT>
```

The analysis script (`scripts/e2e/freetext_crosscheck.py`) uses the BigQuery Python
client via ADC directly and routes quota to `PROJECT`.

---

## Step 1 — Understand what "faithful free-text generation" means

Read these so every finding traces to the code that must change (verify line
refs are current):

1. `packages/sdfb-core/src/sdfb_core/engines/b1_rag/` and
   `packages/sdfb-core/src/sdfb_core/engines/b2_library/` — the free-text
   generation path: `freetext.py`, `profile.py` (shape/length/null modeling),
   and how observed exemplars / sentinels are blended.
2. `packages/sdfb-core/src/sdfb_core/` codegen — how NULL / empty-string and
   length bounds are derived from the reference sample (the empty-parity seam).
3. `config/thresholds.yml` — whether any rule scores free-text repetition /
   sparsity / memorization today (usually it does not — that's a gate blind
   spot worth noting).

Write a one-line "expected behaviour" note: the synthetic column should
reproduce the source's **shape mix**, **length distribution**, **null/empty
fraction**, and **cardinality** — without copying real values verbatim.

---

## Step 2 — Run the crosscheck (single command, all numbers come from it)

```bash
TS=$(date +%Y_%m_%d_%H_%M)
python scripts/e2e/freetext_crosscheck.py \
  --source-fqn    <SOURCE_FQN> \
  --synthetic-fqn <SYNTHETIC_FQN> \
  --columns "<COLUMNS>" \
  --sample-size <SAMPLE_SIZE> --top-k <TOP_K> \
  --out-json "output/freetext_crosscheck/metrics_${TS}.json" \
  --out-md   "output/freetext_crosscheck/report_${TS}.md"
```

When this prompt is chained from the E2E validation report (its Step 3.5),
`--out-json`/`--out-md` are overridden to
`integration_test/<JOB_ID>/freetext_crosscheck_metrics.json` and
`_report.md` so the crosscheck artifacts land next to the rest of that
deployment's evidence — as **working files**: the E2E prompt's Step 6 then
folds them into `integration_test/<JOB_ID>/real/` (verbatim) + `oss/`
(redacted) and prunes the parent-level copies. Run standalone (as above) and
the defaults stay `output/freetext_crosscheck/`.

The script computes, per column, on both tables:

- **Sparsity / cardinality** — row count, null fraction, empty (trimmed)
  fraction, distinct count + ratio.
- **Length distribution** — min / mean / max + p05/p50/p95.
- **Character-class profile** — fraction of sampled values containing
  digits / uppercase / lowercase / whitespace / punctuation.
- **Shape signatures** — each value masked to a class skeleton
  (digit→`9`, upper→`A`, lower→`a`, space→`␣`, punctuation kept literally), in
  an exact per-character view (fixed formats like `9999-99-99`) and a
  run-collapsed view (`9+-9+`). Top-K of each with mass shares.
- **Top literal values** (`APPROX_TOP_COUNT`).

…then **diffs** source vs synthetic into: `shape_recall` (source formats the
synthetic reproduced), `shape_precision` (synthetic formats that exist in
source), `missing_shapes`, `spurious_shapes`, `null/empty/charclass/length`
deltas, and a literal `copy_fraction` (memorization signal), plus a ranked
`findings` list per column and a divergence `score` used to rank the backlog.

The script already emits the `.md`; treat it as the **draft**. Never eyeball
the tables — every number in the final report must match the metrics JSON.

---

## Step 3 — Interpret each signal (what a finding means for the generator)

Map each metric to a concrete generation defect and a code area:

| Signal | Reads as | Likely fix area |
|---|---|---|
| `shape_recall` ≪ 1 | generator never learned real formats (mode collapse to one/few shapes) | shape sampling in `freetext.py` / `profile.py` — sample shapes at observed frequency |
| `shape_precision` ≪ 1 | generator emits impossible formats absent from source | constrain generation to the observed shape support |
| `empty_fraction` / `null_fraction` delta large | generator ignores that the column is mostly empty/NULL in source | empty/null-parity in codegen + engine sentinel handling |
| mean-length `rel delta` large | length distribution not modeled (often a side effect of the empty-parity gap) | length bounds from the reference sample |
| `copy_fraction` high on a **high-cardinality** column | memorization / privacy leak (real values emitted) | exemplar-blend mass / novelty guard |
| `distinct_ratio` collapsed | under-diversification (repeated a handful of templates) | per-batch seeding / pool size |

**Systemic tell**: if *every* column shows `empty_fraction` synthetic ≈ 0 while
source is high, that's one root cause (empty-parity never derived), not N
separate bugs — call it out once as the dominant finding.

---

## Step 4 — Finalize the report

Confirm `output/freetext_crosscheck/report_YYYY_MM_DD_HH_mm.md` contains, in
order:

0. **Header** — source/synthetic FQNs + row counts, columns, sample size,
   generated timestamp, caller identity.
1. **Executive summary** — the ranked backlog table (worst score first) with
   shape recall / precision / copy fraction + the single top issue per column.
2. **Per-column detail** — the source-vs-synthetic metric table, missing +
   spurious shapes, source/synthetic examples, and the severity-tagged findings.
3. **Feeding this to Claude Code** — the closing note pointing each finding at
   the free-text generation code.

If the systemic empty-parity tell (Step 3) is present, add a one-paragraph
**"Dominant root cause"** note near the top of the summary and reference it from
the affected columns. Keep prose tight; every claim backed by a JSON number.

---

## Step 5 — Verify

1. Report + metrics JSON both exist under `output/freetext_crosscheck/` with the
   same timestamp; the report opens and renders.
2. Every headline number in the report matches `metrics_*.json`.
3. Each HIGH finding names a shape/charclass/sparsity number and points at a
   generation code area.
4. Skipped columns (absent from one table) are listed, not silently dropped.
5. Print a one-line summary: report path + the most divergent column (score).

---

## Consistency rules (always enforce)

- **ADC access is a prerequisite** (Step 0). If missing, stop with the exact
  `gcloud auth application-default …` commands — never fabricate live results.
- **Numbers come from `metrics_*.json`**, never from eyeballing a table.
- **Generic always**: columns, datasets, project ids arrive as inputs; the
  script introspects the schema — nothing is hard-coded.
- **Report + metrics share one timestamp** under `output/freetext_crosscheck/`.
- **One systemic root cause beats N duplicate findings** — collapse the
  empty/null-parity gap into a single dominant finding when it spans columns.
- **Copy fraction on high-cardinality free text is a privacy concern**, not a
  fidelity nicety — flag it HIGH.
- No dashboards / Vertex / external LLM suggestions (out of scope per `CLAUDE.md`).

```