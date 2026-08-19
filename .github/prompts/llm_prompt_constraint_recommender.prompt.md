---
mode: agent
description: >
  Recommend and apply per-column {"llm_prompt_constraint": …} objects
  (DDL_CONTRACT_GUIDE §4 / ADR 0024) for a synthetic-dataflow-bigquery table,
  driven by a landed E2E run's evidence bundle
  (integration_test/<JOB_ID>/real/ — crosscheck, stats diff, offline + GCP
  metrics, reports). Pure prompt engineering with teeth: every recommendation
  is evidence-gated, token-economical, validated through the engines' OWN
  parser/renderer, and compatible with both B.1 (RAG) and B.2
  (library-wrapper) plus vLLM guided decoding — the agent re-reads the
  current engine code each run so it can never drift from the codebase.
  Writes the embedded JSON into the schema/DDL file's column descriptions
  (the Terraform-shared source of truth). Runs standalone or chained from
  end_to_end_validation_report_generation.prompt.md Step 8.
  Output: updated schema file(s) +
  integration_test/<JOB_ID>/real/prompt_constraint_recommendations.md +
  its de-identified oss/ twin (standard mapping.json replacements via
  scripts/e2e/redact_doc.py) for agnostic reporting, then refreshed
  _full_report.md recaps in both bundles (build_full_report.py).
---

# /llm_prompt_constraint_recommender — Evidence-driven prompt-constraint authoring

## Goal

Turn one deployment's free-text evidence into the **smallest** set of
`llm_prompt_constraint` objects that closes the observed gaps, and embed them
in the table's column descriptions. The constraints reinforce **both**
engines through one parse site (`contracts/prompt_constraint.py`): they steer
the RAG/pool prompts (retrieval + generation), feed vLLM guided decoding
(`pattern`), extend the novelty rejection set (`examples`), and pin
cross-engine routing (`route`).

This is **prompt engineering as configuration**: every rendered clause is a
per-column constant appended to every pool prompt of every future run — so a
useless clause is a permanent token tax, and a wrong `pattern` is worse than
none (guided decoding *forces* 100 % of generated values into it). Be
precise, minimal, and evidence-gated. Never hand-wave a constraint a number
did not ask for.

---

## Inputs (ask the user if not provided; nothing is hard-coded)

| Param | Example | Notes |
|---|---|---|
| `JOB_ID` | `2026-08-11_06_04_05-9010…` | locates `integration_test/<JOB_ID>/real/` (falls back to the legacy parent-level filenames for pre-bundle job dirs) |
| `SCHEMA` | `config/bq_schema/<dataset>/<TABLE>.schema.json` | the file to edit (repeatable — one per table when the deployment generated several). Also accepts a `*_ddl.json` (edit the `schema[].description` entries) |
| `SOURCE_FQN` | `<project>.<dataset>.<TABLE>` | optional; enables live pattern-coverage verification via ADC when the offline evidence is borderline |
| `TARGET_COLS` | `COL_A,COL_B` | optional; default = every column with a steerable finding in the evidence |
| `DRY_RUN` | `1` | optional; recommend + report only, do not edit `SCHEMA` |

If `SCHEMA` is unknown, derive the table name from
`real/gcp_metrics.json`'s landing/source FQNs and look for the matching
schema file under `config/bq_schema/`; ask the user if none exists.

---

## Step 0 — Prerequisites

ADC is **optional** here: the offline evidence usually suffices. Verify ADC
(same snippet as the E2E prompt's Step 0) only when you need a live coverage
query (Step 3, `pattern`/`values` gates); if it fails, degrade gracefully —
skip live verification and say so in the report, never fabricate coverage.

---

## Step 1 — Ground in the CURRENT engine code (mandatory, every run)

This agent stays correct **by re-deriving the contract from code at run
time** — the tables below are the map, but the code is the territory. Read,
and note anything that changed since this prompt was written:

1. `packages/sdfb-core/src/sdfb_core/contracts/prompt_constraint.py` — the
   `PromptConstraint` model: the exact settable keys, caps
   (`_MAX_CONSTRAINT_CHARS`, `_MAX_PATTERN_CHARS`, `_MAX_EXAMPLES`,
   `_MAX_EXAMPLE_CHARS`, `_MAX_VALUES`), validators, and
   `render_prompt_clause`'s **fixed render order** + final truncation.
   Never emit a key that is not in this model — unknown keys are skipped
   with a WARNING (wasted tokens), and a typo'd **marker** key
   (`lm_…`/`llm_promt_…`) is ignored **silently**
   (`contracts/description_json.py` — the extractor only reacts to the exact
   marker `llm_prompt_constraint`).
2. `packages/sdfb-core/src/sdfb_core/engines/b1_rag/profile.py` +
   `engine.py` — free-text classification thresholds, the `route:"llm"`
   override, `_column_constraint` (clause + measured-length-hint
   suppression), `_build_pool_prompt` (where the clause lands),
   `_pool_json_schema` (`pattern` → guided decoding `items.pattern`), the
   `constraint_examples` rejection set, and the format/collapsed-mask gates.
3. `packages/sdfb-core/src/sdfb_core/engines/b2_library/fidelity.py` +
   `freetext.py` — the B.2 twins of the above; note where the two engines'
   default classification thresholds **differ** (a column can be LLM-routed
   in one engine and categorical in the other — `route:"llm"` is the only
   cross-engine pin).
4. `packages/sdfb-beam/src/sdfb_beam/handlers/vllm_client.py` — how guided
   decoding is actually sent (`response_format: json_schema`, the schema's
   `items.pattern`), `max_tokens`, and the retry ladder. The rendered clause
   is a **per-column constant suffix** after a byte-identical shared
   instruction prefix — that is what keeps vLLM automatic prefix caching hot
   (ADR 0018/0024); anything per-attempt-variable would destroy it.
5. `docs/DDL_CONTRACT_GUIDE.md` §4–§6 (the settable-field table + worked
   examples), `docs/designs/2026-08-10-prompt-constraints.md` §3 (template
   catalog + rendered-prompt anatomy), `docs/adr/0024-…` (decision +
   citations).

Write a five-line "current contract" note: key list, caps, render order,
routing gates, guided-decoding path. Every later decision cites it.

---

## Step 2 — Load the evidence (all numbers come from these files)

From `integration_test/<JOB_ID>/real/` (legacy fallback names in brackets):

| File | What to extract per column |
|---|---|
| `freetext_crosscheck_metrics.json` [`../freetext_crosscheck_metrics.json`] | **primary**: source vs synthetic `shapes`/`shapes_mass` + `shapes_collapsed(_mass)`, `len_p05/p50/p95`, `null/empty_fraction`, `charclass_fraction`, `top_values`, `distinct_ratio`; the `diff` block (`shape_recall`, `shape_precision`, `missing_shapes`, `spurious_shapes`, `charclass_delta`, `mean_length_rel_delta`, `null/empty_fraction_delta`, `copy_fraction`), per-column `findings` + `score` |
| `stats_diff_metrics.json` [`../stats_diff.json`] | `entropy_gap`, `top1_delta`, `length_band` drift, `verdict` |
| `offline_metrics.json` [`../e2e_validation_metrics.json`] | repetition / singularity / sparsity corroboration |
| `gcp_metrics.json` [`../e2e_gcp_metrics.json`] | `copy_ratio*` + freetext rules (`freetext.empty_parity`, `freetext.distinct_floor`, `freetext.copy_fraction`), memorization flags; worker milestones when captured — `generation_plan` / `build_plan_detail` (which route each column actually took, `constraint: bool`), `freetext_pool_built` (`pattern_guided`), `prompt_constraint_*` warnings |
| `report.md`, `freetext_crosscheck_report.md`, `stats_diff.md` | narrative context + already-traced root causes — do not re-derive |

Rank candidate columns by the crosscheck `score` (worst first) intersected
with `TARGET_COLS`. Columns whose findings are **not** steerable by a prompt
(pure sampler/seam defects: empty-parity drift, distinct-ratio collapse from
pool caps, memorization from an LLM fallback) get **no constraint** — name
the owning code area in the report instead. Focus on free-text generation:
the constraint lever is for gaps the *prompt or decoding grammar* can close.

---

## Step 3 — Per-column decision procedure (the ladder)

### 3.0 Evidence gate, then route gate

- **No failing number → no constraint.** Every clause must name the metric
  it is expected to move.
- **Route check first.** A constraint on a column that never reaches the LLM
  is inert. From the `generation_plan`/`build_plan_detail` milestones (or,
  absent those, the profilers' thresholds read in Step 1): constant,
  categorical, temporal-shaped and **identifier-shaped** STRING columns skip
  the LLM. If the typed route already reproduces the marginal — stop, no
  constraint. Recommend `"route": "llm"` (STRING-typed only) **with** a
  constraint when the typed route provably loses semantics (e.g. recall gap
  on a shape-detected column whose mask sampler cannot carry meaning, or a
  column the two engines classify differently and the evidence needs it
  LLM-routed in both).

### 3.1 Key ladder — cheapest effective key first, each with its trigger

| Priority | Key | Evidence trigger | Hard gates |
|---|---|---|---|
| 1 | `pattern` | `shape_recall` low / `spurious_shapes` present AND the source is **shape-rigid**: top exact (or compact alternation of top) shapes cover ≥ 0.95 of non-empty source mass (`shapes_mass`) | anchored `^…$` (the validator does NOT anchor for you); ≤ 200 ch; must `re.compile`; guided-decoding-safe (classes/quantifiers/alternation only — no lookarounds/backrefs); JSON-escape backslashes (`\\.`; **double-escaped `\\\\.`** when the description string itself sits inside a `_ddl.json`) |
| 2 | `prefix` / `suffix` | a literal affix dominates source values (visible in `top_values`/shapes) but synthetic loses it | prose-only steering (no enforcement) — pair with `pattern` when the affix must be guaranteed |
| 3 | `length` | length drift (`mean_length_rel_delta`, `length_band`) | int when source is fixed-width (`len_p05 == len_p95`), else `[p05, p95]`. Setting it **suppresses** the measured length hint — token-neutral or cheaper, but you lose the empirical band, so pin only stable evidence |
| 4 | `values` | closed vocabulary drifting (synthetic emits out-of-set codes) | source `distinct` small (aim ≤ 20; hard cap 64); values render as a Python-list repr inside the clause — a fat list blows the 500-ch clause cap and is truncated mid-list |
| 5 | `format` | shape gap where `pattern` fails its coverage gate, or semantics the mask cannot express | ≤ ~90 ch of business prose naming the format; the workhorse fallback |
| 6 | `charset` / `units` / `locale` | `charclass_delta` on one class / unit drift / wrong-language prose | one short clause each |
| 7 | `examples` | shape alone under-specifies content (semantic prose, composite codes) | **last resort**; 1–2 (cap 8 × 64 ch); MUST be fictitious — they join the pool rejection set, so a real value is a privacy leak AND permanently unusable |
| 8 | `notes` | value legends / business decode worth carrying | renders **last** → first casualty of the 500-ch truncation; keep tiny |

**The `pattern` coverage gate is non-negotiable.** Compute coverage from the
source `shapes_mass` (exact) or `shapes_collapsed_mass` (when only run
lengths vary). Borderline (0.85–0.95) and `SOURCE_FQN` given → verify live:

```sql
SELECT COUNTIF(REGEXP_CONTAINS(<COL>, r'<pattern>')) / COUNT(*) AS coverage
FROM `<SOURCE_FQN>` WHERE <COL> IS NOT NULL AND TRIM(<COL>) != ''
```

Below the gate → downgrade to `format` (a request, not a guarantee). A
partial `pattern` collapses the source's shape mix into the one shape the
grammar allows — it *manufactures* `spurious`/`missing` shape findings.

### 3.2 Token economy (the 500-character budget)

- The whole rendered clause is hard-truncated at 500 ch in **fixed render
  order** (`format; length; charset; prefix; suffix; pattern; allowed
  values; units; locale; fictitious examples; notes`) — verify against
  `render_prompt_clause` (Step 1), and budget load-bearing keys first.
- Never restate what the prompt already carries for free: seed exemplars
  show the dominant shape; the measured length band is appended by default
  (unless `length` suppresses it); the shared instruction already demands
  novel, format-faithful values.
- One key per failure mode; prefer the enforced key (`pattern`) over three
  prose keys saying the same thing. Target ≤ 2–3 keys per column; an empty
  recommendation is a valid (and common) outcome.

### 3.3 Privacy (constraints live in Terraform, git, and logs)

Never paste a real source value into **any** field. `values` only for
non-sensitive enumeration codes; `examples` synthesized from the mask, never
sampled. Before writing, grep every recommended string against the
crosscheck's source `top_values` + `sample_values` — any hit is a leak,
replace it.

---

## Step 4 — Apply to the schema file(s) (skip when `DRY_RUN`)

For each targeted column in `SCHEMA`, set/replace the embedded object in the
`description` string:

- Preserve the human prose around the JSON (prose before/after parses fine —
  the extractor takes the first brace-balanced object carrying the marker).
- Replace an existing `llm_prompt_constraint` wholesale, but carry forward
  still-valid human semantics (usually as `notes` / `values` legends).
- Exact marker spelling `llm_prompt_constraint` — a typo is silently inert.
- Keep the file valid JSON (mind the double-escaping rule for `pattern`).

Remind the user of the propagation chain (guide §7–§8): this file doubles as
the Terraform `google_bigquery_table.schema` payload, and the pipeline reads
descriptions back from `INFORMATION_SCHEMA` via the DDL extractor — the new
constraints only reach the engines after `terraform apply` (or `bq update`)
**and** a fresh `scripts/extract_ddl.py` run, with `--prompt_constraints on`.

---

## Step 5 — Validate through the engines' own parser (mandatory)

Prove every edited description with the real code — no hand-validation:

```bash
python - <<'PY'
import json, re, sys
sys.path.insert(0, "packages/sdfb-core/src")
from sdfb_core.contracts.prompt_constraint import (
    parse_prompt_constraint, render_prompt_clause)
schema = json.load(open("<SCHEMA>"))
cols = schema["schema"] if isinstance(schema, dict) else schema
for col in cols:
    desc = col.get("description") or ""
    pc = parse_prompt_constraint(desc)   # raises DescriptionJsonError loudly
    if pc is None:
        continue
    clause = render_prompt_clause(pc)
    assert not pc.pattern or (
        pc.pattern.startswith("^") and pc.pattern.endswith("$")
    ), col["name"]
    print(f"{col['name']}: {len(clause)} ch :: {clause}")
PY
```

Checks: parses without `DescriptionJsonError`; no unknown-key warnings;
`pattern` anchored + compiles; rendered clause comfortably under 500 ch
(report the count); file still valid JSON. Any failure → fix before writing
the report.

---

## Step 6 — Write the recommendations report (+ its de-identified `oss/` twin)

`integration_test/<JOB_ID>/real/prompt_constraint_recommendations.md`
(**internal** — it names real columns/values). Sections:

1. **Header** — JOB_ID, schema file(s), evidence files read, contract note
   from Step 1 (with any code-vs-doc drift called out).
2. **Recommendation table** — one row per column: evidence numbers → the
   constraint JSON → rendered clause + char count → the metric expected to
   move next run (falsifiable, e.g. "`shape_recall` 0.00 → ≥ 0.9",
   "`spurious_shapes` → empty") → risk notes (coverage %, route change).
3. **Declined columns** — evidence seen, why no constraint (not steerable /
   typed route sufficient / sampler-owned defect + owning code area).
4. **Propagation checklist** — terraform/bq update → extract_ddl → run flags
   (`--prompt_constraints on`, `--prompt_debug redacted` for the first
   debug run; grep worker logs for `freetext_pool_prompt` + `sha12` drift,
   and `prompt_constraints_found` listing the columns).

Then produce the **de-identified twin** with the bundle's standard mapping —
this is the version to share/report outside the environment (column names →
`COL_NNN`, values → `VAL_NNNN`, identifiers hidden, so results stay
agnostic while remaining correlatable with the job's other `oss/` artifacts):

```bash
python scripts/e2e/redact_doc.py \
  --mapping integration_test/<JOB_ID>/real/mapping.json \
  --in  integration_test/<JOB_ID>/real/prompt_constraint_recommendations.md \
  --out integration_test/<JOB_ID>/oss/prompt_constraint_recommendations.md
```

It applies exactly the replacements `real/mapping.json` records (the same
ones every other `oss/` file already carries) and exits non-zero if a known
identifier/column survives — the twin is only shareable after
`leak scan: clean ✅`. On a pre-bundle job dir with no `real/mapping.json`,
skip the twin and say so in the summary (never hand-redact). Note the
mapping only knows tokens the bundle export registered: keep the report's
constraint JSON free of real values anyway (Step 3.3 — fictitious
`examples`, non-sensitive `values`), because a fresh literal invented here
has no `VAL_NNNN` entry to hide behind.

Close by refreshing each bundle's one-file recap so the recommendations
fold into `_full_report.md` (ToC + every `.md` + ```json metrics annexes):

```bash
python scripts/e2e/build_full_report.py \
  --dir integration_test/<JOB_ID>/real \
  --dir integration_test/<JOB_ID>/oss
```

---

## Consistency rules (always enforce)

- **Code beats this prompt.** Field set, caps, render order, routing and
  decoding behavior are re-read from the engine code every run (Step 1);
  when they disagree with the tables above, follow the code and flag the
  drift in the report.
- **Evidence-gated** — every key cites the failing number it will move; no
  speculative constraints; an empty recommendation is a valid outcome.
- **Coverage-gated `pattern`** — ≥ 0.95 non-empty source mass (offline or
  live) or downgrade to `format`.
- **Token-economical** — ≤ 2–3 keys per column, budget the 500-ch clause in
  render order, never duplicate what the prompt already carries.
- **Privacy-first** — no real values in constraints; grep recommendations
  against source literals before writing. Share only the
  `oss/prompt_constraint_recommendations.md` twin (Step 6, standard
  `mapping.json` replacements + clean leak scan) outside the environment;
  `real/` stays local.
- **Both engines** — one parse site guarantees identical parsing; use
  `route:"llm"` when their default classifications diverge and the evidence
  needs the LLM route in both.
- **Numbers come from the `real/` JSONs**, never from eyeballing tables; no
  Vertex / dashboards / external LLM suggestions (out of scope per
  `CLAUDE.md`).
