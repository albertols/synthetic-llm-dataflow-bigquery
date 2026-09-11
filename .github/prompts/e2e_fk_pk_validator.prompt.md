---
mode: agent
description: >
  Verify, for ONE Dataflow job and ONE relationship model file, that primary-key
  and foreign-key enforcement actually happened in the landed BigQuery tables.
  Reads the model through the repo's RelationshipRegistry (so the checked
  contract is exactly what the launch used: enabled tables, widened edges,
  driving/implied roles), resolves the job to its run ids and landing tables
  from the launcher/worker log, then runs READ-ONLY queries with the local `bq`
  CLI (ADC): PK duplicates per table, orphans per enforced edge (whole-tuple
  join), identity uniqueness, and the child-per-parent fan-out against the
  histogram the launch measured. Every SQL result is cross-checked against the
  in-DAG measurement in `validation_runs.dlq_by_rule`; disagreement is a
  finding. Inputs: MODEL_FILE (default config/relationships/example_retail.yaml),
  JOB_ID, PROJECT, LANDING_DATASET, QUALITY_DATASET, REGION. Output:
  output/e2e_fk_pk_validation_<JOB_ID>_YYYY_MM_DD_HH_MM.md +
  runs/<JOB_ID>/real/fk_pk_validation.json (+ oss/ twin, aliases only).
  Never writes to BigQuery. Never fabricates a number.
---

# /e2e_fk_pk_validator — did PK and FK enforcement actually happen?

## Goal

Answer one question with live numbers: **for job `JOB_ID`, does every landed
table satisfy the PK/FK/identity contract declared in `MODEL_FILE`?**

Three evidence sources, in this order, and the report says which one each
number came from:

1. **The model file**, parsed by the repo's own registry — never by hand. The
   registry is what the launch used: it hides disabled tables, drops
   documented (`enforced: false`) edges, assigns one of the five roles
   `driving` / `implied` / `independent` / `conditional` / `external`
   (ADR 0036/0037) and **widens** a parent's edge with the columns a child
   pins (`derived_widenings`, ADR 0036). Checking the raw YAML instead would
   verify a contract the launch never enforced.
2. **The launch's own record** — the launcher/worker log for `JOB_ID`
   (`launch_config`, `relationship_model` card, `fk_edge_role`,
   `fk_edge_widened`, `fk_fanout_measured`, `fk_key_pool_bound`,
   `fanout_bound`, `rows_detail`) and the `validation_runs` rows it wrote.
3. **The landed tables**, queried READ-ONLY through the local `bq` CLI.

This is a repeated action after every relational launch. Be deterministic,
print the SQL you ran and the raw counts, and keep the shareable output
anonymised (aliases only — `A_TABLE`, `B_COL_006`, … — per
`config/relationships/README.md`; real names may appear only in `real/`).

---

## Inputs (ask the user if not provided; nothing is hard-coded)

| Param | Example | Notes |
|---|---|---|
| `MODEL_FILE` | `config/relationships/example_retail.yaml` | the relationship model the launch ran with. A directory is accepted (the loader scans it and skips `example_*.yaml` samples); a single file is loaded as-is. `example_star_diamond.yaml` is the multi-parent sample (ADR 0037) |
| `JOB_ID` | `2026-09-10_11_00_44-8177138577202163642` | the Dataflow job id (one relational single-job launch = N tables) |
| `PROJECT` | `<project-id>` | GCP project of the landing dataset |
| `LANDING_DATASET` | `<project>.synthetic_data` | where `<TABLE>` landed (the model's bare table names are the landing table names) |
| `QUALITY_DATASET` | `<project>.synthetic_data_quality` | `validation_runs` + `dlq` |
| `REGION` | `europe-west3` | Dataflow region (for `gcloud` log fetches) |
| `LOG_FILE` | `integration_tests/<JOB_ID>/worker_logs.jsonl` | optional; when absent, Step 2 fetches the job's logs with `gcloud logging read` |
| `RUN_ID_COL` | `run_id` | optional; when the landing tables accumulate runs (`write_disposition=append`), every SQL is scoped to this column's values from `launch_config.run_ids`. With `overwrite` (the default) the whole table IS the run |
| `MAX_BYTES` | `50000000000` | `--maximum_bytes_billed` guard for every query (50 GB default) |

---

## Step 0 — PREREQUISITES: `bq` + ADC, fail fast

```bash
which bq gcloud || echo "MISSING: install the Google Cloud SDK"
gcloud auth application-default print-access-token >/dev/null && echo "ADC OK"
bq --project_id="$PROJECT" query --use_legacy_sql=false --format=json \
   'SELECT 1 AS ok' | grep -q '"ok"' && echo "bq OK"
```

If any line fails, STOP and print the exact remediation
(`gcloud auth application-default login`,
`gcloud auth application-default set-quota-project $PROJECT`,
`gcloud config set project $PROJECT`). Never continue with fabricated results.

Every query below runs as:

```bash
bq --project_id="$PROJECT" query --use_legacy_sql=false --format=json \
   --maximum_bytes_billed="$MAX_BYTES" "$SQL"
```

Read-only by construction: the prompt issues `SELECT` only. If a table in the
contract does not exist (`Not found`), record `landed=false` for it and keep
going — a missing table is a finding, not a crash.

---

## Step 1 — The contract, from the registry (not from the YAML)

Run from the repo root (laptop or M4; pure Python, no GCP):

```bash
uv run --no-sync python - "$MODEL_FILE" <<'PY'
import json, sys
from sdfb_beam.io.relationships import load_relationship_registry
from sdfb_core.contracts.relationships import RelationshipError

reg = load_relationship_registry(sys.argv[1])
out = {"sha": reg.sha12(), "tables": {}, "widenings": reg.derived_widenings()}
for model in reg.models:
    for name, rel in model.tables.items():
        entry = {"enabled": rel.enabled, "pk": list(rel.pk),
                 "identity": list(rel.identity), "edges": [], "not_enforced": [],
                 "driving_choice": None}
        roles = {}
        if rel.enabled:
            try:
                roles = reg.edge_roles(name)
                entry["driving_choice"] = reg.driving_choice(name)
            except RelationshipError as exc:
                entry["roles_error"] = str(exc)
        for e in reg.enforced_edges(name):          # widened, both ends enabled
            entry["edges"].append({"cols": list(e.cols), "ref": e.ref,
                                   "ref_cols": list(e.ref_cols),
                                   "role": roles.get(e, "?"),
                                   "overlap": list(reg.edge_overlap(name, e)),
                                   "rest": list(reg.edge_rest(name, e))})
        for e in rel.fk:                            # declared, never drawn
            if not e.enforced:
                why = "documented"
            elif not e.external and not reg.enabled(e.ref):
                why = "parent disabled"
            else:
                continue
            entry["not_enforced"].append({"cols": list(e.cols), "ref": e.ref, "why": why})
        out["tables"][name] = entry
enabled = tuple(t for t, r in out["tables"].items() if r["enabled"])
try:
    out["waves"] = [list(w) for w in reg.generation_waves(enabled)]
except RelationshipError as exc:
    out["waves_error"] = str(exc)
print(reg.card(enabled[0]), file=sys.stderr)      # pipes and arrows, never mermaid
print(json.dumps(out, indent=2))
PY
```

Save stdout as `runs/<JOB_ID>/real/contract.json` (the card goes to stderr so the JSON stays parseable). What it means:

- **Verify**: every `enabled: true` table's `pk`, `identity`, and each
  `edges[]` entry (these are the widened, enforced edges — for a driven child
  the `driving` edge's columns may be LONGER than the YAML's `cols`; that is
  the inherited-column widening and it IS the contract).
- **Read the role, never guess it** (ADR 0037). `role` is one of
  `driving` / `implied` / `independent` / `conditional` / `external`;
  `overlap` names the columns a `conditional` edge shares with the driving
  edge (empty for every other role) and `rest` the ones joined in — an
  empty `rest` on a conditional edge is a pure existence filter.
  `driving_choice` says how the driving edge was picked: `single`,
  `marked` (`drives: true`), `derived` (most-derived parent, widened), or
  `first_declared` — the default when nothing else decided. A
  `first_declared` table should have logged `fk_driving_edge_defaulted`
  (WARNING) at launch: report it as "the launch defaulted", not as a
  defect, and note that one `drives: true` pins it.
- **Report, do not verify**: `not_enforced[]` (documented edges, edges to a
  disabled parent) and `enabled: false` tables. A launch never drew keys for
  them; an orphan count there is meaningless.
- **Stop and say so**: a `roles_error` or `waves_error` means this model
  could not have launched; either the wrong file was given or the launch used
  another one — Step 2's sha check settles it.

---

## Step 2 — The launch's own record

Locate the log: `LOG_FILE` if given, else
`integration_tests/<JOB_ID>/worker_logs.jsonl`, else fetch:

```bash
gcloud logging read \
  "resource.type=dataflow_step AND resource.labels.job_id=\"$JOB_ID\"" \
  --project="$PROJECT" --format=json --limit=200000 \
  | python -c 'import json,sys; [print(json.dumps({"ts":e.get("timestamp"),"sev":e.get("severity"),"msg":e.get("textPayload") or json.dumps(e.get("jsonPayload"))})) for e in json.load(sys.stdin)]' \
  > "integration_tests/$JOB_ID/worker_logs.jsonl"
```

Extract, with one `grep`/`python` pass over `msg` (entries are single-line
`SDFB_MILESTONE name=… k=v` except the `relationship_model` card):

| Milestone | Take | Use |
|---|---|---|
| `launch_config` | `run_ids`, `landing_tables`, `num_rows`, `write_disposition`, `uniqueness_mode`, `driven_uniqueness_mode` | scopes every later query; the run-id prefix is `<run_id>` up to `-NN-<TABLE>` |
| `relationships_loaded` / `relationship_model … sha=` | `sha` | must equal `contract.json.sha`. **Mismatch = the launch used a different model file than `MODEL_FILE`; report it and continue, but label every verdict "against a different contract"** |
| `fk_edge_role edge= role= overlap=` | per edge | must match `contract.json` roles AND `overlap` (ADR 0037) |
| `fk_driving_edge_defaulted table= edge= hint=` (WARNING) | per table | rule 4 fired — expect it on every table whose `driving_choice` is `first_declared`, and ONLY those. Present without a matching `first_declared`, or absent with one, means the launch read a different model |
| `fk_edge_overlap_external table= edge= overlap=` (WARNING) | per edge | an external parent's edge overlaps the driving edge — integrity on that edge is NOT resolved; Step 5 records it `unverifiable-by-design`, never a PASS |
| `relational_fk_edge … mode=side_input\|conditional overlap=` (worker) | per edge | the DAG path each non-driving edge took; a `conditional` role with `mode=side_input` (or the reverse) is a wiring finding |
| `fanout_bound … conditional= candidate_cap=` (worker) | per driven table | how many conditional edges rode with the keys and the `--fk_candidate_cap` in force |
| `batch_unmatched batch_id= keys_dropped=` (worker) | per batch | driving keys dropped before generation for a non-nullable conditional edge — sum them and compare with `fanout / keys_unmatched` and `fk.unmatched` |
| `fk_edge_widened table= ref= via= added=` | the derived widening | must match `contract.json.widenings` |
| `fk_fanout_measured edge= parents= children= mean= p50= p95= max= zero_share=` | the SOURCE fan-out | Step 5 compares the landed fan-out to it |
| `relational_single_job … rows_detail=` | derived rows per table | Step 3 compares to `valid_count` |
| `fk_key_pool_bound columns= key_tuples= weighting=` (worker) | side-input edges | which edges used the ADR 0031 pool path |
| `fanout_bound driving_cols= cells= exact_cells=` (worker) | driven children | `exact_cells=True` ⇒ PK unique by construction is CLAIMED for that table — Step 4 tests the claim |
| `BlockerThresholdExceeded` / `Workflow failed` | failure | the job failed: still run every check on whatever landed, and say the job failed |

If the log holds none of these, the job predates ADR 0036 (or is not a
relational launch): run Steps 3–6 on the model's enabled tables anyway and
say the launch record is absent.

---

## Step 3 — `validation_runs`: what the DAG measured

```sql
SELECT run_id, landing_table, status, num_rows_requested, valid_count,
       dlq_count, blocker_count, observed_blocker_ratio, dlq_by_rule, created_at
FROM `${QUALITY_DATASET}.validation_runs`
WHERE run_id LIKE '${RUN_ID_PREFIX}%'
ORDER BY landing_table, created_at DESC
```

One row per table (keep the newest per `landing_table`). Parse `dlq_by_rule`
(JSON string) and record per table: `pk.duplicate`, `fk.orphan`,
`fk.unmatched`, `identity.unique`, `row.duplicate`, `engine_failure`.
`fk.unmatched` (ADR 0037 ruling B) counts rows NEVER GENERATED because a
non-nullable `conditional` edge had no candidate for their driving key —
it is weighted by each dropped key's expected rows, so it is a row count,
not a key count (`fanout / keys_unmatched` is the key count). A run whose
model declares no conditional edge must not have the key at all; absent is
the expected reading, 0 is fine, and a missing key on a conditional model
means the rule never fired. Note the uniqueness
mode: under `streaming` (the driven-child default) `pk.duplicate` is
MEASURED, not removed, so a non-zero value here means duplicate PKs LANDED —
Step 4 must find them. Missing rows = the table never reached its summary
(job failed before it, or the table was not part of the job).

---

## Step 4 — PK and identity, per enabled landed table

For each `enabled: true` table `T` with `pk = [p1, …, pn]`:

```sql
-- PK duplicates (composite-safe; NULL-safe via TO_JSON_STRING)
SELECT COUNT(*) AS total,
       COUNT(DISTINCT TO_JSON_STRING(STRUCT(p1, …, pn))) AS distinct_pk,
       COUNT(*) - COUNT(DISTINCT TO_JSON_STRING(STRUCT(p1, …, pn))) AS pk_duplicates,
       COUNTIF(p1 IS NULL OR … OR pn IS NULL) AS pk_null_rows
FROM `${LANDING_DATASET}.T`
[WHERE ${RUN_ID_COL} IN UNNEST(@run_ids)]
```

and for each identity column `i`:

```sql
SELECT COUNT(*) - COUNT(DISTINCT i) AS identity_duplicates,
       COUNTIF(i IS NULL) AS identity_nulls
FROM `${LANDING_DATASET}.T` [WHERE …]
```

Pass criteria: `pk_duplicates = 0`, `pk_null_rows = 0`,
`identity_duplicates = 0`. Also record `total` vs Step 3 `valid_count` and vs
`rows_detail` (driven tables) — a landed count far below the derived
expectation with an empty DLQ is a silent-drop finding (see
`keys_dropped_null` in the playbook).

---

## Step 5 — FK, per enforced (widened) edge

For each `edges[]` entry of an enabled child `C` → parent `P`, with
`cols = [c1, …, ck]` and `ref_cols = [r1, …, rk]` (same k; the WHOLE tuple is
the unit of integrity, ADR 0031 — never check columns one by one):

```sql
-- orphans: child tuples with no parent tuple; NULL tuples are legitimately
-- parentless and excluded
WITH parent AS (
  SELECT DISTINCT r1 AS k1, …, rk AS kk FROM `${LANDING_DATASET}.P` [WHERE …]
),
child AS (
  SELECT c1 AS k1, …, ck AS kk FROM `${LANDING_DATASET}.C` [WHERE …]
  WHERE c1 IS NOT NULL AND … AND ck IS NOT NULL
)
SELECT COUNT(*) AS child_rows,
       COUNTIF(p.k1 IS NULL) AS orphans,
       SAFE_DIVIDE(COUNTIF(p.k1 IS NULL), COUNT(*)) AS orphan_ratio
FROM child c LEFT JOIN parent p USING (k1, …, kk)
```

Pass criterion: `orphans = 0`. For an **external** edge (`ref` is
`dataset.table`) the parent is that FQN as given; if it is not readable,
record `unverifiable` with the error. An external edge the launch logged
`fk_edge_overlap_external` for is `unverifiable-by-design`: the driving
edge overwrote its shared columns, so a non-zero orphan count there is
the KNOWN limitation (ADR 0037 §9), not a regression — report it with
the remedy (enable the parent inside the launch).

**A `conditional` edge is checked with the SAME query.** Its role changes
how the values were produced (the shared `overlap` columns came from the
driving key, the `rest` from a co-partitioned join), not what integrity
means: the whole tuple must exist in the parent, so run exactly the
orphan query above over `cols`/`ref_cols` and expect `orphans = 0`. The
NULL tuples that query already excludes are the **ruling-B rows** — a
driving key whose shared value had no candidate, with every `rest` column
NULLABLE, lands with `rest = NULL` and is legitimately parentless. Count
them and report them as their own line (they are NOT orphans and NOT
`fk.unmatched`, which covers the non-nullable case where the key was
dropped instead):

```sql
SELECT COUNTIF(c1 IS NULL AND … AND ck IS NULL) AS null_tuples,
       COUNT(*) AS child_rows
FROM `${LANDING_DATASET}.C` [WHERE …]
```

An edge whose `rest` is empty (a pure existence filter) has no nullable
case at all: every unmatched key was dropped, so `null_tuples` must be 0
and the losses appear only in `fk.unmatched`.

**Fan-out reproduction** (driven children only — the edge whose role is
`driving`, when `fk_fanout_measured` exists for it):

```sql
WITH per_parent AS (
  SELECT r1, …, rk, COUNT(c.c1) AS k
  FROM (SELECT DISTINCT r1, …, rk FROM `${LANDING_DATASET}.P`) p
  LEFT JOIN `${LANDING_DATASET}.C` c ON c.c1 = p.r1 AND … AND c.ck = p.rk
  GROUP BY r1, …, rk
)
SELECT COUNT(*) AS parents, SUM(k) AS children, AVG(k) AS mean,
       APPROX_QUANTILES(k, 100)[OFFSET(50)] AS p50,
       APPROX_QUANTILES(k, 100)[OFFSET(95)] AS p95, MAX(k) AS max,
       SAFE_DIVIDE(COUNTIF(k = 0), COUNT(*)) AS zero_share
FROM per_parent
```

Compare `mean`, `p95`, `max`, `zero_share` with the launch's
`fk_fanout_measured` values: the landed ratio is by construction, so a
`mean` off by more than the sampling noise of `parents` (≈ 2/√parents
relative) is a finding, and a `max` above the source's `max` is impossible
by construction and therefore a generator regression.

Documented / disabled edges: list them under "declared, not enforced" with
no verdict. If both columns sets exist in the landed DDLs you MAY run the
orphan query as **informational** — label it so; it is not a pass/fail.

---

## Step 6 — Cross-check: SQL vs the DAG's own measurement

Build one table, one line per (table, check):

| table | check | SQL | validation_runs | agree? |
|---|---|---|---|---|
| `C_TABLE` | pk.duplicate | 0 | 0 | ✅ |
| `A_TABLE` | fk.orphan `(A_COL_001,A_COL_002,A_COL_003)->C_TABLE` | 0 | absent (fanout edge: gate skipped by design) | ✅ |
| … | … | … | … | … |

Rules for the last column:

- SQL 0 and DAG 0/absent-by-design → ✅.
- SQL > 0 and DAG 0 → ❌ **and** a second finding: the in-DAG measurement
  missed it (name the mode: `exact` barrier vs `streaming` measurement).
- SQL 0 and DAG > 0 under `exact` → ✅ (the barrier removed them; the count
  is the DLQ) — say so.
- SQL 0 and DAG > 0 under `streaming` → ❌ contradiction: streaming does not
  remove rows, so a non-zero measurement with a clean table means the two
  looked at different data (run-id scoping, overwrite, or a second launch).

**`fk.unmatched` is not `fk.orphan` — they have opposite expectations.**
Give each conditional edge its own line and read them this way:

| rule | what it counts | expected? |
|---|---|---|
| `fk.orphan` | a row LANDED whose whole FK tuple has no parent | **never** — the draw is joint by construction, so any non-zero value is a generator regression and a BLOCKER |
| `fk.unmatched` | a row was NEVER GENERATED: the driving key's shared value had no candidate in the conditional parent, and the `rest` columns are not all NULLABLE, so the key was dropped before generation (ADR 0037 ruling B) | **expected when the SOURCE lacks that branch** — it is an input fact. Corroborate it: `SELECT COUNT(*) FROM (SELECT DISTINCT <overlap> FROM parent_of_the_driving_edge) EXCEPT DISTINCT (SELECT DISTINCT <overlap cols> FROM the conditional parent)` — if the source really is missing those shared values, the counts line up and the verdict stays PASS with a noted input gap; if the source HAS them, the join lost them and that is a defect |

Cross-check the three views of the same event before judging: the DLQ row
count (`fk.unmatched`), the Beam counter (`fanout / keys_unmatched`, KEYS
not rows) and the summed `batch_unmatched keys_dropped=` lines must agree
on the key count. Two of them disagreeing is itself the finding. A
non-zero `fanout / candidates_dropped_null` alongside them means the
conditional parent landed NULLs in the shared columns — that is where to
start, not the join.

---

## Step 7 — Verdict and artifacts

**Verdict** (first line of the report): `PASS` when every enabled landed
table has `pk_duplicates = 0`, `identity_duplicates = 0`, every enforced edge
has `orphans = 0`, and every driven edge's fan-out is within noise; otherwise
`FAIL` with the failing (table, check) pairs listed first. A non-zero
`fk.unmatched` does NOT by itself make the verdict `FAIL` — it fails only
when the source DOES hold the missing shared values (Step 6); otherwise it
is a PASS with a named input gap, reported with its row count. `INCOMPLETE` when
a table in the contract did not land or the job failed — still list every
check that could run.

Write:

- `output/e2e_fk_pk_validation_<JOB_ID>_YYYY_MM_DD_HH_MM.md` — verdict, the
  registry card from Step 1 (pipes and arrows, never mermaid), the Step 6
  table, then per-table and per-edge sections each with the exact SQL and the
  raw JSON result, the launch-record cross-checks (sha, roles, widenings,
  fan-out), and a "declared, not enforced" list.
- `runs/<JOB_ID>/real/fk_pk_validation.json` — `{"job_id", "model_file",
  "model_sha", "launch_sha", "verdict", "tables": {T: {"landed", "total",
  "pk_duplicates", "identity": {...}, "validation_run": {...}}}, "edges":
  [{"child", "parent", "cols", "ref_cols", "role", "overlap", "rest",
  "orphans", "child_rows", "null_tuples", "unmatched",
  "fanout": {...}}], "not_enforced": [...], "queries": [...]}` with real
  names; and `runs/<JOB_ID>/oss/fk_pk_validation.json` + the report's `oss/`
  twin with every real table/column/project name replaced by its alias
  (`runs/history_mappings_replacement.json` convention; add any
  new name to the mapping before writing).

Never fabricate: a query that failed is recorded as `error` with the message;
a table that did not land is `landed=false`. Numbers appear once, in the
JSON, and the markdown quotes them.

---

## Worked shapes (the committed samples)

### 1 — a chain (`config/relationships/example_retail.yaml`)

Through Step 1 it yields:

- `A_TABLE` — root: PK `(A_COL_001, A_COL_002)`, identity `A_COL_009`.
- `B_TABLE` — PK `B_COL_001`; enforced edge `(B_COL_006, B_COL_007) → A_TABLE
  (A_COL_001, A_COL_002)` — role `driving`.
- `C_TABLE` — PK `C_COL_001`; enforced `(C_COL_004) → B_TABLE (B_COL_001)` —
  `driving`; `(JOIN_KEY) → A_TABLE` is **documented** → listed, not verified.
- `D_TABLE` — `enabled: false` → listed, not verified; its edge to `C_TABLE`
  not drawn.
- `E_TABLE` — PK `E_COL_001`; `(E_COL_002) → warehouse_ds.EXT_TABLE (EXT_ID)`
  is **external** → the orphan query runs against that FQN if readable.

So the checks are: 4 PK checks (A, B, C, E), 1 identity check (A), 3 orphan
checks (B→A, C→B, E→external), 2 fan-out comparisons (B→A, C→B) when the
launch measured them, and 2 "declared, not enforced" lines.

### 2 — a star and a diamond (`config/relationships/example_star_diamond.yaml`)

The multi-parent shapes (ADR 0037). Step 1 yields two components:

- **star** — `dim_a` (PK `A_KEY`) and `dim_b` (PK `B_KEY`) are roots;
  `fact` (PK `(A_KEY, B_KEY, LINE_NO)`) has two enforced edges:
  `(A_KEY) → dim_a` with role `driving` and
  `driving_choice = "first_declared"` (no `drives:` marker, no ancestry
  between the dims — the launch logged `fk_driving_edge_defaulted`), and
  `(B_KEY) → dim_b` with role `independent`, `overlap = []` (it rode the
  side-input key pool, so `fk_key_pool_bound` names it and the
  `fk.orphan` gate measured it).
- **diamond** — `top` (PK `T`) is the root; `left` (PK `(T, L)`) and
  `right` (PK `(T, R)`) each drive off `top`; `bottom` (PK `(T, L, R)`)
  has `(T, L) → left` as `driving` (marked `drives: true`) and
  `(T, R) → right` as `conditional` with `overlap = ["T"]`,
  `rest = ["R"]` — the worker logged
  `relational_fk_edge mode=conditional overlap=T` for it.

So the checks are: 7 PK checks (one per table), 0 identity checks, 6
orphan checks — `fact→dim_a`, `fact→dim_b`, `left→top`, `right→top`,
`bottom→left`, and `bottom→right` as the SAME whole-tuple query on
`(T, R)` — plus, on `bottom→right`, the `null_tuples` count (ruling-B
rows, expected only if `R` is NULLABLE) and the `fk.unmatched` line
cross-checked against `fanout / keys_unmatched`. Every `fact` row must
carry a `B_KEY` that exists in `dim_b`, and `bottom`'s single `T` must
satisfy `left` AND `right` — that one column is the whole point of the
conditional role, so an orphan on `bottom→right` with none on
`bottom→left` means the join, not the pool, is the defect.

---

## Non-negotiables

- `bq` is the only path to BigQuery here; every statement is a `SELECT`
  guarded by `--maximum_bytes_billed`. No `bq load`, no `bq rm`, no DML.
- The contract comes from the registry (Step 1), never from reading the
  YAML by eye — the widened edges and the roles are what the launch enforced.
- Aliases only outside `real/`. Real table, column, dataset and project names
  never reach `oss/` or the chat transcript.
- Report what could not be verified as `unverifiable`/`landed=false`; do not
  downgrade the verdict to PASS by omission.
