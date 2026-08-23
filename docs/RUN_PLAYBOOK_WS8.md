# WS8 E2E run playbook — validating ADR 0021 + 0022 from scratch

The campaign companion to [`RUN_PLAYBOOK.md`](RUN_PLAYBOOK.md) (GPU verdict,
Dataflow options, L4 capacity ladder, report recipe — all still apply and are
not repeated here). This doc is the **run matrix for the WS8 features**:
relational contract + FK pools (ADR 0021), tiered `source_table_stats`,
exact-distinct pool sizing, B.2 inverse-CDF sampling, measured length hints,
shape-preserving expansion (ADR 0022 / [design](designs/2026-08-05-source-table-stats.md)
— each `--source_stats` / `--freetext_expansion` mode has its own diagram
panel there and in
[`2026-08-05-freetext-expansion-modes.md`](designs/2026-08-05-freetext-expansion-modes.md);
not redrawn here).

Model note for this campaign (qwen on T4): keep `vllm_dtype=float16` (the
DAG default — qwen ships bf16, T4 is CC 7.5) and `vllm_max_model_len=8192`;
if staging a qwen2.5 build, remember the local-dir rename before GCS staging
and that a missing `special_tokens_map.json` is expected and handled.

---

## 0. From-scratch reset (once, before R1)

Deploy/align the stats table first (the ws8 schema added
`sample_rows`/`stats_tier`/`profiler_version`):

```bash
# new table:
bq mk --table "${PROJECT}:synthetic_rag.source_table_stats" \
  config/bq_schema/synthetic_rag/source_table_stats.schema.json
# pre-existing table (additive, no data rewrite):
bq update "${PROJECT}:synthetic_rag.source_table_stats" \
  config/bq_schema/synthetic_rag/source_table_stats.schema.json
```

Then wipe state so every store path is exercised cold:

```bash
for t in \
  "synthetic_data.<LANDING_A>" "synthetic_data.<LANDING_B>" \
  "synthetic_data_quality.dlq" "synthetic_data_quality.validation_runs" \
  "synthetic_rag.freetext_pools" "synthetic_rag.rag_chunks" \
  "synthetic_rag.source_table_stats"; do
  bq query --use_legacy_sql=false "TRUNCATE TABLE \`${PROJECT}.${t}\`"
done
```

Truncating `freetext_pools`/`rag_chunks`/`source_table_stats` resets every
digest — **all first runs are cold** (full pool ladder + embed + stats
write). That is the point of the campaign, but it is also the expensive
part: sequence warm runs immediately after their cold twin.

Preflight (step 11 must now report the stats table OK, not ACTION):

```bash
python scripts/deployment_prerequisites.py --project ${PROJECT} \
  --source-stats-table ${PROJECT}.synthetic_rag.source_table_stats
```

## 1. Campaign map

```mermaid
flowchart LR
  classDef cpu   fill:#1baf7a,color:#fff,stroke:#127a55
  classDef gpu   fill:#7a3fd1,color:#fff,stroke:#5a2f9d
  classDef store fill:#2a78d6,color:#fff,stroke:#1d5599

  R0["⚙️ R0 fake smoke<br/>plumbing after truncate"]:::cpu --> R1["🧠 R1 cold defaults<br/>b1 · 1M · table A"]:::gpu
  R1 --> R2["⚙️ R2 warm twin<br/>skip-key + store hits"]:::cpu
  R2 --> R3["🧠 R3 exact stats<br/>pool-size lift"]:::gpu
  R3 --> R4["⚙️ R4 expansion arms<br/>off vs all"]:::cpu
  R4 --> R5["🧠 R5 B.2 engine<br/>inverse-CDF live"]:::gpu
  R5 --> R6["🧠 R6 table B cold<br/>+ FK child (ADR 0021)"]:::gpu
  R6 --> R7["🧠 R7 10M scale<br/>warm everything"]:::gpu
  R1 -.-> ST[("🗄️ source_table_stats<br/>pools · chunks")]:::store
  ST -.-> R2
```

Order matters twice: R2 must follow R1 *without* clearing anything (it
proves the skip keys), and R3/R4-off must clear `freetext_pools` for the
digest first or the persisted pool masks the effect being measured (the
§6c do-not-confound rule of the main playbook).

## 2. Run matrix

Common to every vLLM run: `client_type=vllm`, qwen + `vllm_dtype=float16`
(DAG default), `vllm_max_model_len=8192`, `identity_cols`/`pk_cols` set to
the table's real columns, fresh Airflow trigger per run (salted `run_id` is
derived). `batch_size=1000` for 1M/10M rows (WS5 T3; the DAG's `16` default
is sized for smokes). Params not listed = DAG defaults (which already carry
the WS8 posture: `source_stats=sample`, `freetext_expansion=identifiers`,
`prompt_constraints=on`, `build_pool_layer=true`, `build_rag_layer=true`,
`uniqueness_mode=exact`, `pool_seed_strategy=centroid`).

| Run | Trigger config (overrides only) | Validates | Expect |
|---|---|---|---|
| **R0** smoke | `{"client_type":"fake","num_rows":"10000","batch_size":"16"}` | plumbing after truncate: preflight, landing/dlq/validation_runs writes, stats write path — no GPU | `source_stats_written` (tier `sample`, `profiler_version=2`, `__table__` row present); `validation_runs.valid_count=10000` |
| **R1** cold baseline | `{"num_rows":"1000000","batch_size":"1000"}` — pure defaults otherwise | the default WS8 posture end-to-end on table A: sample-tier stats, pool build + persist, chunk build, expansion=identifiers | one `vllm_ready`; `pool_branch_emitted`; `source_stats_written`; `freetext_pool_built` per LLM column; landing = 1M |
| **R2** warm twin | identical to R1, re-trigger | append-only skip semantics + every store's read path | `source_stats_skipped`; `pool_build_skipped` + `freetext_pool_store_hit`; `b1_chunks_reused`; **zero** vLLM spawns (12.6-min-class run); output differs from R1 (salted run_id) |
| **R3** exact stats | `{"num_rows":"1000000","batch_size":"1000","source_stats":"exact"}` — **first**: `DELETE FROM freetext_pools WHERE reference_digest='<digest>'` | Tier 2: one aggregate scan; tier-aware skip key (sample rows exist, exact must still write); exact-distinct pool sizing | `source_stats_exact` with column count; NEW stats rows `stats_tier='exact'`; pool targets ≥ R1's on sample-starved columns (compare `freetext_pool_built target=`); job survives even if the scan fails (`source_stats_exact_failed` → sample tier) |
| **R4a** expansion off | `{"num_rows":"1000000","batch_size":"1000","freetext_expansion":"off"}` (pools warm — expansion is post-pool CPU) | the July diversity ceiling, reproduced on purpose (control arm) | crosscheck: freetext `distinct == pool size` again |
| **R4b** expansion all | same with `"freetext_expansion":"all"` | maximum diversity: shape expander + digit-run mutation on texty draws | crosscheck: distinct ≫ pool size, shapes still match `shape_mix`; zero extra LLM calls vs R4a |
| **R5** B.2 engine | `{"engine":"b2_library","num_rows":"1000000","batch_size":"1000"}` | inverse-CDF numeric+temporal live (ADR 0022 acceptance 5); B.2 empty-parity + length hints | numeric/temporal decile overlap vs source beats the July uniform baseline (KS drop in crosscheck); `temporal_range_clamped` where applicable |
| **R6** table B + FK | table B cold: `{"table_fqn":"<TABLE_B>", "num_rows":"1000000","batch_size":"1000"}` then, if B declares FKs to A in its `{"sdfb":1,...}` description contract: add `"fk_parent_landing":"${PROJECT}.synthetic_data"` | second-table generality of R1 + ADR 0021 referential integrity (child FK columns sample A's landed keys) | preflight P1–P5 pass on the contract; constraint-discovery lines in driver log; post-run orphan query (§4) = **0 rows** |
| **R7** 10M scale | `{"num_rows":"10000000","batch_size":"1000"}` — warm digest, all stores populated | throughput at scale with the full WS8 posture (July 10M: 26.4 min / ~6.3k rows/s, but `distinct==pool size`) | ≥ 6k rows/s class; the diversity ceiling GONE (expansion default on); stats skipped (warm digest) |

Optional control if length-hint attribution is ever questioned:
`{"prompt_constraints":"off"}` with cleared pools — pool prompts lose the
DDL constraint *and* the measured length band together (one gate), so
compare `len_p50` parity in the crosscheck against R1.

Multi-table alternative for R6: `scripts/run_tableset.py` orders the set
parent-first from the contracts and runs the same single-table job per
table (dry-run first).

## 3. New milestones to read out (WS8 additions to §6d/§7c)

```bash
grep -o 'name=[a-z_]*' worker_logs.jsonl | sort | uniq -c | sort -rn
```

| Milestone | Reads as |
|---|---|
| `source_stats_written` / `source_stats_skipped` | stats landed / versioned skip key hit (digest + `profiler_version` + achieved tier) |
| `source_stats_exact` | Tier-2 aggregate scan merged; `source_distinct` threaded to pool sizing |
| `source_stats_exact_failed` (WARNING) | exact scan failed; run degraded to sample tier and stays retryable — investigate, not fatal |
| `temporal_range_clamped` | a temporal column's floor hit now−10y; its decile vector was clamped too |
| `freetext_pool_built target=` | compare per-column targets R1 vs R3 — the exact-distinct lift |
| `generation_plan` (detail) | per-column route + null/empty/shapes/constraint/expandable — the first thing to check when a column misbehaves |
| `prompt_constraints_found` (launcher + worker) | the `llm_prompt_constraint` clauses actually fetched from the DDL: rendered clause + `clause_sha12` per column (launcher = preflight over the schema, worker = per generation plan). Diff `clause_sha12` across launches to verify a Terraform edit landed |
| `prompt_constraint_unknown_keys` (WARNING) | a description carries a typo'd/newer constraint key — now names the `column=` |
| `identifier_source_filter size=` (+ `_absent`/`_error`) | ADR 0025: an identifier column's FULL source domain feeds its mask table + novelty rejection (wave 4: support-only — weights stay row-mass) |
| `numeric_source_filter size=` (+ `_absent`/`_error`) | ADR 0026: an identity-like INT64 column's full domain feeds the draw-time collision scrub |
| `numeric_source_rejected collisions= nudged= unresolved=` | ADR 0026: per-column scrub outcome (first batch); `unresolved > 0` = fully dense neighborhood, read with the k-anon exemption in mind |
| `freetext_pool_skipped_expandable` | ADR 0026: the column draws from its shape mix — no pool built, by design (not a store outage) |
| `build_info commit=` (launcher + workers) | ADR 0027: the image's git commit — compare builds BEFORE comparing runs |
| `ddl_live_extracted` | ADR 0027: the LIVE `INFORMATION_SCHEMA` schema (bqClient) is what this launch generates from — BigQuery metadata edits always take effect |
| `target_metadata_overlaid constraint_columns=` / `target_metadata_unavailable` (WARNING) | ADR 0027 D2: constraints + contract come from the LANDING table's descriptions; unavailable = NO constraints this run (source descriptions are never a substitute) |
| `ddl_live_extract_failed` (WARNING) → `ddl_loaded_from_uri fallback=True` | ADR 0027: live extraction unreachable — OFFLINE mode, the pin's (possibly stale) constraints/contract apply |
| `ddl_pin_drift` (WARNING) / `ddl_pin_fresh` / `ddl_pin_check_error` | ADR 0027: the `--ddl_uri` pin vs live, checked while live is authoritative — drift means the OFFLINE FALLBACK is stale; re-extract before the next air-gapped day |
| `llm_route_unused` (WARNING) | ADR 0027: this setup ran zero LLM ladders — GPU workers idle; plan a CPU-only rerun |
| `freetext_pool_binary_fallback` | ADR 0027: control-char column skipped the LLM ladder for the template fallback (COL_048-class) |
| `numeric_kanon_filter size=` (+ `_absent`/`_error`) | ADR 0027: the scrub's keep-set from SOURCE frequencies (HAVING COUNT ≥ 10); absent = sample-heuristic fallback |
| `freetext_pool_source_filter size=` | ADR 0023: the column's FULL source domain is in the pool rejection set |
| `freetext_pool_source_filter_absent` / `_error` (WARNING) | domain above cap / store error — pool built with sample-only rejection; check copy_fraction post-run |
| `pool_taint_rebuild` (WARNING, launcher) | warm pools overlapped the live source → deleted + rebuilt clean (expected ONCE per tainted pre-ADR-0023 digest) |
| `pool_taint_check_error` / `pool_taint_delete_error` | preflight could not verify/clear — warm pools kept, verify copy_fraction post-run |
| `vllm_unfittable_wait` (WARNING) | VRAM transiently short — in-process re-measure instead of a bundle retry (2026-08-05 R1 cost ~80 s + a setup cycle) |
| `fk_enforcement_summary` (launcher; **WARNING** when 0 enforced) | ADR 0031 D5: what this launch will actually enforce, per table, BEFORE the GPU spends. An `ENFORCEABLE` line = an informational edge whose columns exist on both sides; the block prints the exact contract edit |
| `fk_key_pool_bound columns= key_tuples= weighting= null_fraction=` | ADR 0031: one per enforced edge, worker-side. `weighting=child_marginal` = the IPF fit ran (the child's marginals survive the restriction); `uniform` = no overlap between the child's sample and the parent's keys — check the edge is the one you meant |
| `fk_key_pool_capped` (WARNING) | ADR 0031 D6: the parent holds at least the 100k side-input cap of distinct keys — the child references a uniform sample of them, so its FK distinct count cannot exceed the cap |
| `fk.orphan` in `validation_runs.dlq_by_rule` | ADR 0031 D4: rows that referenced a non-existent parent. Non-zero = a generator regression (the draw is joint by construction) — read it as a BLOCKER, not a tolerance |

## 4. WS8 pass criteria (on top of the main playbook's §2 list)

1. **Stats contract** — every row this campaign wrote:

   ```sql
   SELECT stats_tier, profiler_version, COUNT(*) n,
          COUNTIF(sample_rows IS NULL) missing_sample_rows
   FROM `${PROJECT}.synthetic_rag.source_table_stats`
   GROUP BY 1, 2;
   -- expect: (sample, 2) rows from R0/R1/R6; (exact, 2) rows from R3;
   -- missing_sample_rows = 0 everywhere
   ```

2. **Privacy gate** — no literal values persisted for high-cardinality
   columns:

   ```sql
   SELECT `column` FROM `${PROJECT}.synthetic_rag.source_table_stats`
   WHERE `distinct` > 50
     AND JSON_VALUE(stats, '$.top_values[0][0]') IS NOT NULL;
   -- expect: zero rows
   ```

3. **Skip-key tiers** — after R3, the same `(table_fqn, reference_digest)`
   must hold BOTH tiers (sample from R1, exact from R3): the tier-aware
   `exists()` worked; a single-tier result means the R1 rows blocked R3.
4. **FK integrity** (R6 child) — orphan target is zero. Composite edges
   join on the WHOLE tuple (ADR 0031); NULL FK tuples are legitimately
   parentless and excluded:

   ```sql
   -- single-column edge
   SELECT COUNT(*) FROM `${PROJECT}.synthetic_data.<LANDING_B>` c
   LEFT JOIN `${PROJECT}.synthetic_data.<LANDING_A>` p
     ON c.<FK_COL> = p.<PK_COL>
   WHERE p.<PK_COL> IS NULL AND c.<FK_COL> IS NOT NULL;

   -- composite edge: DISTINCT projection of the parent's ref columns
   SELECT COUNT(*) AS orphans
   FROM `${PROJECT}.synthetic_data.<LANDING_B>` c
   LEFT JOIN (SELECT DISTINCT <REF_COLS>
              FROM `${PROJECT}.synthetic_data.<LANDING_A>`) p
     USING (<REF_COLS>)
   WHERE p.<FIRST_REF_COL> IS NULL
     AND c.<FIRST_FK_COL> IS NOT NULL;
   ```

   **Read the launcher first, before the money is spent** (ADR 0031 D5):
   `fk_enforcement_summary` states `N enforced · M informational` for
   every table. `0 enforced` with an `ENFORCEABLE` line means the
   contract still carries `"informational": true` on an edge whose
   columns exist on both sides — fix the description and relaunch;
   nothing downstream can produce integrity from a display-only edge.
   Cross-check on the worker side: `fk_key_pool_bound` (one per enforced
   edge, `weighting=child_marginal`, `key_tuples=N`) and the absence of
   `fk.orphan` in `validation_runs.dlq_by_rule` — the in-DAG BLOCKER now
   measures what the SQL above verifies independently.

5. **B.2 marginals** (R5) — in the crosscheck/report, numeric and temporal
   columns' decile overlap vs source improves on the July uniform baseline;
   a synthetic amounts column no longer lands flat.
6. **Diversity ceiling** (R4b, R7) — freetext `distinct` is no longer
   pinned at pool size; shapes still conform to the observed `shape_mix`.
7. **thresholds.yml freetext rules** — `freetext.empty_parity`,
   `freetext.distinct_floor` pass; `freetext.copy_fraction` (BLOCKER) at 0.

After each run, the standard three-command report recipe
([`RUN_PLAYBOOK.md` §5](RUN_PLAYBOOK.md)) plus
`scripts/e2e/freetext_crosscheck.py` for the per-column fidelity readout;
interpretation is `e2e-interpreter`'s job as usual.

## 5. 2026-08-05/07 four-run findings → shipped remediations

The R1 (A_TABLE + B_TABLE, 1M cold) + R7 (both tables, 10M warm) cycle
landed four code fixes; what to expect on the next runs:

| Finding (run) | Fix | Next-run readout |
|---|---|---|
| B_TABLE pools memorized 33–99% of 10 columns; R7 replayed them at 10M ([ADR 0023](adr/0023-source-domain-pool-rejection.md)) | full-source-domain rejection at build + launcher taint preflight | first B_TABLE launch: `pool_taint_rebuild` then a clean cold build; A_TABLE (clean pools) keeps `pool_build_skipped`; crosscheck `copy_fraction = 0` |
| A_TABLE 10M: 24 generate batches stalled 120–908 s in `strptime` (global parser lock, 16 threads × 7 temporal formats) | lock-free `temporal_parse.py` on every profiler/sampler parse path | `batch_done seconds=` p99 collapses to the p50 class; no `Operation ongoing … GenerateRecordsDoFn` warnings |
| 4/13 B_TABLE columns reproduced 0% of source shapes under the relaxed fallback (leading-space padding lost) | `_shape_fallback_pool` templates from `shape_mix` (literal padding preserved) | crosscheck shape recall > 0 on COL_026-class columns |
| 13 `vllm_max_model_len_unfittable` aborts + ~80 s bundle retry on T4 | in-process wait-and-re-measure (6 × 20 s window) | `vllm_unfittable_wait` instead of a failed-bundle cycle |

Cost note from R7: a fully-warm run (`pool_build_skipped` + every store
hit) never ignites vLLM — the T4s idle for the whole run. Warm replays of
an already-built digest can drop the GPU worker pool entirely
(CPU-only `n1-highmem-8` runs the same DAG; the embedder already demotes).

Deferred, with rationale: B.1 numeric decile-KS drift (14 A_TABLE + 8
B_TABLE columns) is the designed marginal-blend ceiling — **R5 (B.2
inverse-CDF) is the acceptance run**, and B.2 needs the ADR 0023 seam
wired before its memorization numbers are read as engine truth; COL_048
(binary-garbage source column) stays accepted as-is.

### §5b Verification + second remediation wave (2026-08-07 A_TABLE R1, job `…09_44_36-8456…`, image `cc8cf7d`)

The first post-fix cold run **verified all four §5 fixes**: batch
`seconds=` p50 1.2 / p99 7.1 / max 8.4 (the 120–908 s stall class is gone,
zero stuck-bundle warnings); `freetext_pool_source_filter` fired per LLM
column (19,815 / 1,298 / 3,030 values, no `_absent`/`_error`); all pools
hit target via shape top-up (no stagnation, no undersize); one clean vLLM
spawn. It also exposed the next findings, remediated 2026-08-08:

| Finding (job `…09_44_36`) | Fix | Next-run readout |
|---|---|---|
| Probe scored COL_048 62.8% "copies" on a 62.4%-empty column — empty-parity (and, ahead: head-value) re-emission counted as memorization; the same artifact inflated the 2026-08-05 B_TABLE 33–99% numbers | `copy_ratio_substantive` (excludes NULL/trimmed-empty/date-sentinels; k-anonymity floor: source values with freq ≥ 10 are enum mass) — scored by `memorization_flags` + `freetext.copy_fraction` | memorization table lists substantive ratios; empty-heavy columns stop false-flagging CRITICAL |
| COL_053/COL_054 miss their dominant literal (`ZZ3000`/`BATCH`, 77%+ share) — ADR 0023 rightly bans it from pools, nothing re-emits it | `head_values` on FREE_TEXT profiles (share ≥ 5%, count ≥ 10, top 8) re-emitted at observed frequency after null/empty | crosscheck shape recall > 0.75 on both; `top1_share` parity |
| COL_001 reproduces 0% of source masks — collapsed identifier template merges variant masks into digit+upper | identifier route draws from `shape_mix` when top masks cover ≥ 50% of distinct values (random-mask ids keep the collapsed template) | crosscheck: synthetic masks ⊆ source masks on rigid-mask columns; COL_064-class diversity unchanged |
| Pool branch logs `freetext_pool_store_absent` (its own by-design blank) — misread as store outage in two reports | `ctx.pool_branch` suppresses the WARNING inside the branch | warning appears only when no `--freetext_pools_table` was passed |

### §5c Before FK/PK + stress runs — measured watch-list (not code changes)

- **Pool build is the cold-run bottleneck** (`PoolTrigger` 14.9 min = 43%
  of stage time here; 22.4 min on B_TABLE): all ladders run on ONE worker
  while the fleet idles. If cold-run wall time starts to matter, split the
  branch per column (`Create(columns) → Reshuffle → per-column ladder`) so
  each GPU ignites once and builds its share — design change, measure first.
- **Launcher phase is ~8.6 min** (`launcher_start → workers_starting`):
  dominated by the Flex launcher VM pulling the multi-GB single image
  (ADR 0009). Accepted cost; revisit only if launch latency matters.
- **Uniqueness at 10M+ is stress-ready** — `CombinePerKey` first-wins keeps
  memory flat (WS6 W4); no change needed for PK runs.
- **FK pools cap** — `io/fk_pools.py` loads ≤ 100k parent keys per edge into
  the pickled context; fine for R6-scale parents, and composite-FK joint
  tuples remain the recorded M2 limitation.
- **Per-row pydantic cost** — the engine validates each record and the DoFn
  re-dumps it (`model_validate` + `model_dump` × 10M). Candidate CPU win,
  but it changes DLQ semantics — profile on M4 before touching.
- **B.2 parity (R5 gate)** — ✅ done 2026-08-08: `FreeTextHook` consults the
  ADR 0023 `SourceValueStore` in its pool novelty filter and shape fallback
  (same `freetext_pool_source_filter*` milestones as B.1). The store rides
  the generate path (`source_values_table` → worker-side attach, fetches
  process-cached) because B.2 builds pools lazily in Generate workers — no
  pool branch. The reference blend stays: it is confined to ≤100-distinct
  enum columns, the same category-reuse the substantive copy metric exempts.
  R5 memorization numbers are now engine-comparable.

### §5d Third remediation wave (2026-08-11 R1 pair → shipped on ws8)

The two R1 cold baselines (`…04_07_50-11228…` A_TABLE, `…06_04_05-9010…`
B_TABLE, 1M rows each) were read column-by-column across both bundles'
stats-diff, crosscheck, probe metrics and worker logs. Every failing
column reduced to five engine defect classes + three tooling gaps —
design + decisions in [ADR 0025](adr/0025-marginal-fidelity-by-construction.md)
and `docs/designs/2026-08-20-marginal-fidelity-wave3.md`:

| Finding (both R1s) | Fix | Next-run readout |
|---|---|---|
| 22 numeric columns at decile-KS 0.40–0.90 (A: 14+3 warn, B: 8); COL_009-class 40-prefixed band broken mid-range — the anchored+uniform VALUE-AVERAGE is a convolution, one in-range outlier hands uniform mass the whole span | B.1 numeric = inverse transform sampling through the full sorted observed sample (`_fidelity.py`); `similarity` no longer shapes numerics | `stats_diff` decile-KS ≤ 0.2 (new `numeric.decile_ks` rule) on every B.1 numeric column; COL_009 keeps its leading-40 band |
| ~25 categorical columns with entropy gaps −0.2…−0.99 (COL_004-class: source ~all EUR, synthetic near-uniform) — the substantive similarity blend flattened every skewed enum at the default 0.5 | categorical draws follow the empirical frequency table at ANY similarity | `stats_diff` entropy_gap ≈ 0 / top1_delta ≈ 0 on categorical columns |
| COL_001/COL_005 lost their literal `E2F3`-class prefix; COL_064 lost the UUID v4 version/variant nibbles (mask fill drew from column-wide alphabets) | `positional_alphabets` narrow every mask-table fill per position; singleton positions pin as literals | crosscheck: synthetic values carry the fixed prefix; COL_064 emits valid v4 |
| COL_001 mask recall 0.38 — mask table only ever saw the ≤10k reference sample | identifier columns pull their full source domain through the ADR 0023 store (`identifier_source_filter` milestone); rejection set covers the whole keyspace | shape recall on identifier columns rises toward source-mask coverage; novelty vs full domain by construction |
| Row-mass inversions: COL_054 digit-delta +0.22, B_TABLE COL_024 92% `99`-mask vs source 32%, COL_015 48% hallucinated alpha mass — `shape_mix`/mask weights counted DISTINCT values, not rows, and head values were double-counted | `build_shape_mix` input = rows minus heads (top_k 8→32); `build_mask_table` weights = row occurrences; all-literal buckets no longer count toward the mix-coverage pivot | crosscheck shape-share deltas ≈ 0 on COL_054/COL_024/COL_015-class columns; COL_015 `format_rejected` > 0 (gate re-armed) |
| Tooling: 5 false `freetext.copy_fraction` BLOCKERs on day-granularity temporal columns; crosscheck 0.27-vs-probe-0.000 split on COL_015; `_full_report.md` had no supported small variant (the R1 recaps were deliberately shared annex-free for size, leaving the ToC over-promising) | day-granularity exemption (tagged, visible, passing); enum-reuse carve-out (`copy_fraction` vs `copy_fraction_raw`); `build_full_report.py --annexes list` | copy_fraction section reads clean on temporal columns; the two copy metrics agree; small recaps regenerate with a consistent ToC |

B.2 keeps its distinct-weighted mix this wave (its coverage pivot divides
by the deduped pool; row plumbing lands with R5 — ADR 0025 §Consequences).
`generation_plan.columns_detail` now carries `expandable` per column, so
the next postmortem reads the draw path instead of re-deriving it.

### §5e Fourth wave (2026-08-20 R1 pair → shipped on ws8)

The 2026-08-20 R1 cold pair (`…05_49_25-7855…` A_TABLE, `…06_28_11-7047…`
B_TABLE) VERIFIED ADR 0025's numeric+categorical fixes (112/112 columns
`decile_ks` ok; B_TABLE zero `memorization_flags`). The residual findings
split into measurement artifacts and four real defects — design +
decisions in [ADR 0026](adr/0026-measurement-first-mask-integrity.md) and
`docs/designs/2026-08-20-measurement-and-mask-integrity-wave4.md`:

| Finding (both R1s) | Fix | Next-run readout |
|---|---|---|
| Most "shape-mass" findings (COL_054 75→54, COL_024 68.6→7.7 "inversion", COL_015 "49% spurious alpha") were the crosscheck's `RAND()<p LIMIT` storage-front sample — exact full-table shares matched synthetic within 2 pp; COL_015 "contamination" retracted (`DEVOLUCION T` is a genuine dominant source value, heads re-emit it by design) | crosscheck samples hash-ordered (deterministic); new `shape_mass_tv` + `missing_shapes_below_floor` diff keys | crosscheck `shape_mass_tv` ≈ 0 on COL_024/COL_054-class; `missing_shapes` never empty at recall < 0.9 |
| CRITICAL: COL_009 (INT64, 34.6k source-distinct) `copy_ratio_substantive` 0.52 — dense-band inverse-CDF interpolation rounds onto rare real account numbers | identity-like integral columns (>100 sample-distinct) fetch the full domain (ADR 0023 seam) + draw-time scrub (redraw + ±8 nudge; multi-knots kept as enum mass) | `numeric_source_filter` fires; COL_009 substantive ≪ 0.3; `memorization_flags` empty |
| COL_064 recall 0.005 / COL_001 recall 0.46 — 1024-mask cap collapse + lexicographic digit-skewed tie-break + 1/recall renormalization | Good–Turing tail bucket (per-position char frequencies) + crc32 tie-break; domain now support-only (fixes the ADR 0025 E5 distinct-weighting regression) | COL_064 shape plateau gone; COL_001 top-mask share ≈ source; novelty vs full domain holds |
| COL_038 invents `'6C'`/`'F4'` codes — hex class bound to letter-only positions (digit leak 10/16); retry mass migration (COL_026 0.890→0.838) | `_class_for` kind preservation; mask-stable fill retries (bucket never re-picked) | COL_038 family split ≈ source; COL_026 dominant mask share restored |
| Pool build = 41% (A) / 53% (B) of cold wall time, partly for pools never read (expandable columns draw from shape mix) | expandable columns skip the ladder (`freetext_pool_skipped_expandable`); constraint columns never expand (clause + `pattern` reach the tail again) | PoolTrigger share drops on B_TABLE-class; skipped columns keep `distinct ≫ 512` |
| 15 false `freetext.copy_fraction` BLOCKERs on INT64 small/medium domains; stall ladder unattributable to a column | `exempt_numeric_domains` (tagged, visible; CRITICAL stays `memorization_flags`); probe keeps per-column `pool_ladder` timestamps | copy_fraction section reads clean on numerics; topup/stagnation attributable per column |

### §5f Wave-4 verification cycle (2026-08-20/21 four-run pair → shipped on ws8)

Two wave-4 cold pairs (A_TABLE `…14_13_44-17334…` / `…07_58_08-12248…`,
B_TABLE `…14_39_00-1599…` / `…07_23_07-4791…`) VERIFIED every ADR 0026
acceptance criterion (figures: `make_wave4_verification_figures.py`;
decisions: [ADR 0027](adr/0027-verified-wave4-operational-integrity.md)):
COL_009 substantive 0.522 → 0.253 identically on both cold runs; COL_064
plateau 0.25% → 0.045%; COL_001 top-mask at source parity; false
BLOCKERs 16 → 2. The cycle's own findings → fixes:

| Finding (four-run cycle) | Fix | Next-run readout |
|---|---|---|
| Constraint edits live in BigQuery metadata never reached ANY launch — the pinned `--ddl_uri` predates them; zero `prompt_constraints_found`, silently | **live-first DDL resolution** (ADR 0027 D2): every launch extracts schema + descriptions from live `INFORMATION_SCHEMA`; the pin demotes to the offline fallback, its staleness reported (`ddl_pin_drift`/`ddl_pin_fresh`) | `ddl_live_extracted` + `prompt_constraints_found` with the expected `clause_sha12` on the FIRST launch after a metadata edit — no re-extraction step needed |
| No way to tell which BUILD a job ran (interpreter mis-filed the deterministic scrub as "sampling variance" and the E5 route change as "non-determinism") | `build_info commit=` milestone from launcher + every worker (`SDFB_BUILD_COMMIT` baked at image build) | first log lines carry the commit; reports compare builds before runs |
| Scrub v1: redraw-first redistributed rejected mass (COL_047 decile-KS 0.038 → 0.166) and the sample multi-knot exemption kept pseudo-enum values (COL_009 0.25 vs ~0.14 telemetry residual) | nudge-first (±24, in-quantile) + keep-set from SOURCE frequencies (`fetch_frequent`, HAVING COUNT ≥ 10) | COL_047-class decile-KS back to ≤0.1; COL_009 substantive → ~0.14 floor; `numeric_source_rejected` gains `redrawn=`; `numeric_kanon_filter size=` fires |
| B_TABLE cold run never ignited vLLM (all 13 columns expandable — by design) yet billed ~28 idle GPU-min; COL_048's binary ladder burned 8.6 min of format-rejected LLM calls | `llm_route_unused` WARNING when a setup runs zero ladders; `is_binary_class` columns go straight to the template fallback (`freetext_pool_binary_fallback`) | plan for CPU-only reruns on `llm_route_unused`; A_TABLE cold pool phase drops by the COL_048 ladder time |
| `shape_mass_tv` saturates at ~1.0 on near-unique-mask columns (COL_064 0.956) — would rank the healthiest identifier columns worst | `shape_head_tv` (named head shapes + grouped tail) carries findings/score/summary; raw TV stays in the diff | crosscheck exec summary shows Head TV; COL_064/COL_001 read ≈ 0 there |
| oss redaction never covered the Dataflow environment dump (staging buckets, KMS, subnets, network tags in every bundle; one bundle skipped column redaction entirely) | probe masks infra params at collection (`_job_params` sanitizer); existing bundles scrubbed + re-mapped | new bundles carry `gs://REDACTED_BUCKET/…`-style params only |

## 6. Next-cycle recipes — R1-c (constraints), R6 (table B + FK), R7 (10M)

Written from the 2026-08-20 R1 cold pair (jobs `…05_49_25-7855…` A_TABLE,
`…06_28_11-7047…` B_TABLE — bundles under `integration_tests/`). Both runs
verified ADR 0025's numeric/categorical fixes end-to-end (A: 67/67, B:
45/45 columns `decile_ks` ok; B additionally raised **zero**
`memorization_flags`). Both also ran with **no PK declared** — every next
run below closes that gap via the description contract.

### 6a. R1-c — constraint acceptance rerun (both tables)

Verifies the four `llm_prompt_constraint` edits recommended by the
2026-08-20 bundles (`prompt_constraint_recommendations.md` in each):
A_TABLE `COL_064` (route:llm + RFC 4122 v4 `pattern`); B_TABLE `COL_042`
(same), `COL_015` (corrected `format`, `pattern` declined at 52% live
coverage), `COL_019` (skeleton-anchored `format` + 2 fictitious
`examples`).

```
1. terraform apply           # description edits on the LANDING tables
   -- (synthetic_data.*, the tables YOU own — never the source/lake
   -- tables, whose descriptions are stripped by design) — and that is
   -- ENOUGH to reach the next launch: live-first DDL resolution
   -- (ADR 0027 D2) overlays the target table's descriptions from
   -- INFORMATION_SCHEMA every time. (The 2026-08-21 cycle predated
   -- this: four runs consumed a stale pin and fired zero
   -- prompt_constraints_found.)
2. OPTIONAL housekeeping: python scripts/extract_ddl.py … + re-upload the
   ddl_uri pin — only to keep the OFFLINE fallback fresh for air-gapped
   days; `ddl_pin_drift` reminds you when it drifts.
3. DELETE FROM `${PROJECT}.synthetic_rag.freetext_pools` WHERE reference_digest IN (
     SELECT DISTINCT reference_digest
     FROM `${PROJECT}.synthetic_rag.source_table_stats`
     WHERE table_fqn IN ('<A_FQN>','<B_FQN>'))
   -- REQUIRED: the pool skip-key is the REFERENCE digest (row content),
   -- which a description-only edit does NOT change — a warm store hit
   -- would replay pools built with the OLD prompts and mask the edit
   -- (§1 do-not-confound rule). rag_chunks/source_table_stats can stay.
4. Trigger R1 config per table: {"num_rows":"1000000","batch_size":"1000"}
5. Verify BEFORE reading any metric: `build_info commit=` matches the
   image you built; `ddl_live_extracted` + `target_metadata_overlaid
   constraint_columns=N` (a `target_metadata_unavailable` launch ran with
   NO constraints — check the landing table exists and carries the
   descriptions; an `ddl_live_extract_failed` → pin-fallback launch does
   NOT carry fresh edits); then `prompt_constraints_found` with the
   expected clause_sha12 per column.
```

Readout ladder (in order, before any crosscheck):

| Check | Expect |
|---|---|
| launcher `prompt_constraints_found` | `clause_sha12` **changed** for `COL_015`/`COL_019`; **newly present** for `COL_064`/`COL_042` |
| worker `generation_plan.columns_detail` | `COL_064`/`COL_042` on `route=llm` (previously identifier route) |
| crosscheck A `COL_064` | post-ADR-0027: the engine's mask tail already serves valid v4 for free (plateau 0.25%→0.045% verified) — judge by `shape_head_tv` ≈ 0, NOT recall. The `route:"llm"`+`pattern` constraint remains optional (guided decoding buys exactness at one LLM call per pool value); consider REMOVING it to keep the cheaper mask route |
| crosscheck B `COL_019` | `shape_recall` 0.33 → ≥ 0.7; the two named templates appear in synthetic top shapes |
| crosscheck B `COL_015` | with the wave-4 crosscheck (hash-ordered sampling), the alpha shapes REAPPEAR in the source panel — they are genuine head values (`DEVOLUCION T` 31.9% of source non-empty; ADR 0026 §Context). Expect `shape_precision` ≈ 1 and head shares within ~2 pp; the constraint now steers only the digit-code TAIL (constraint columns no longer expand) |

### 6b. R6 — table B + FK child (full config recipe)

**Contract (Terraform).** Declare relationships on BOTH tables' **LANDING
twins** (`synthetic_data.a_table` / `synthetic_data.b_table` — ADR 0027
D2: the pipeline reads description surfaces from `--landing_table`, never
the source) — the parent needs its `pk` so `--uniqueness_mode=exact`
gives the child a duplicate-free key pool, and both R1 baselines flagged
the undeclared-PK gate blind spot:

```hcl
locals {
  a_table_contract = jsonencode({
    sdfb = 1
    pk   = ["ACCOUNT_ID"]              # activates pk.duplicate gate + clean parent keys
  })
  b_table_contract = jsonencode({
    sdfb = 1
    pk   = ["MOVEMENT_ID"]
    fk = [{
      cols     = ["ACCOUNT_ID"]
      ref      = "core_banking.a_table"   # dataset-qualified, ALWAYS (P1 stops otherwise)
      ref_cols = ["ACCOUNT_ID"]
    }]
  })
}
```

`ref` names the SOURCE-world `dataset.table`; at run time only the table
name is reused — the read targets `{fk_parent_landing}.{a_table}`
(`io/fk_pools.py::parent_landing_fqn`). Full worked examples + sequence
diagram: [`DDL_CONTRACT_GUIDE.md`](DDL_CONTRACT_GUIDE.md) §6–§8 (not
redrawn here).

**Trigger config (child run — overrides only):**

```json
{"table_fqn": "<TABLE_B>", "num_rows": "1000000", "batch_size": "1000",
 "fk_parent_landing": "${PROJECT}.synthetic_data"}
```

`fk_parent_landing` is `project.dataset` (no table) — the landing dataset
holding the parent's already-landed synthetic rows. It is the activation
switch: contract `fk` **and** this flag must both be present
(`run_pipeline.py::_load_reference_and_preflight`), otherwise the FK
column silently keeps its profiled marginal.

**Preconditions checklist (in order):**

1. Terraform applied; contracts visible on the LANDING table:
   `bq show --format=prettyjson ${PROJECT}:synthetic_data.<TABLE_B>` —
   description carries the `{"sdfb":1,…}` JSON (P1 parses it, P2 checks
   the columns; a marked-but-broken contract is a loud `SystemExit`).
   Expect `target_metadata_overlaid` in the launcher log.
2. OPTIONAL: `extract_ddl.py` re-run against the LANDING table to refresh
   the `ddl_uri` offline-fallback pin (live overlay is authoritative).
3. **Parent landed and non-empty**: `SELECT COUNT(*), COUNT(DISTINCT
   ACCOUNT_ID) FROM ${PROJECT}.synthetic_data.a_table` — run A first in
   this campaign and do NOT truncate `synthetic_data.<LANDING_A>` between
   the parent run and R6. An empty parent = empty `fk_pools` = the FK
   override silently NOT applied (engine skips empty pools) → orphans.
4. Parent-key cap awareness: `io/fk_pools.py` loads ≤ 100k DISTINCT
   parent keys per edge. A 1M-row parent with > 100k distinct keys is
   fine — the child samples a 100k subset, referential integrity holds,
   the orphan query stays 0.
5. Keep DAG defaults: `uniqueness_mode=exact`, `prompt_constraints=on`.

**Milestones to grep (on top of §3):** `relational_contract_loaded
pk=MOVEMENT_ID fk_count=1`, `fk_pool_loaded
parent=${PROJECT}.synthetic_data.a_table values=N` (N ≤ 100k),
`preflight_pk_not_unique_in_sample` (WARNING-only — real sources may
violate an undeclared PK).

**Pass criteria:** §4.4 orphan query = 0 rows, plus `validation_runs`
gate with `pk.duplicate` now ACTIVE. **Expected v1 side-effect, not a
defect:** the FK column's profile is overridden to a uniform categorical
over the parent pool (ADR 0021: integrity beats the child marginal), so
`stats_diff` on `ACCOUNT_ID` may show entropy/top1 drift — read it as the
documented v1 trade-off, not a regression. Composite-FK joint tuples stay
the recorded M2 limitation.

Multi-table alternative: `scripts/run_tableset.py` (parent-first
ordering, dry-run first).

### 6c. R7 — 10M scale (warm everything)

**Trigger config:** `{"num_rows":"10000000","batch_size":"1000"}` — same
table(s), same digest.

**Preconditions:** all stores populated and NOT truncated since the last
cold run (`freetext_pools`, `rag_chunks`, `source_table_stats`); no
source-table content change (a content change moves the reference digest
and silently makes R7 a cold 10M run — the 2026-08-20 cold pair measured
pool build at 41–53% of wall time, which at 10M cold would dominate the
run). Verify warmth first: the §2 R2 readouts (`pool_build_skipped`,
`freetext_pool_store_hit`, `b1_chunks_reused`, `source_stats_skipped`)
must all fire.

**Expect:** ≥ 6k rows/s class (July 10M: 26.4 min at ~6.3k rows/s);
diversity ceiling gone (`freetext_expansion` default); `batch_done
seconds=` p99 in the p50 class (lock-free strptime, §5); a fully-warm run
may never ignite vLLM (§5 cost note — the GPU pool can be dropped for
warm replays). Re-score memorization at 10M: collision metrics scale with
row count, so `copy_ratio_substantive` on COL_009-class columns is THE
number to re-read at scale.
