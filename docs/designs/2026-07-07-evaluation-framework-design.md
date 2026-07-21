# Design — Evaluation framework (`synthetic_data_quality.validation_data_history`)

- **Status**: implemented (WS3, 2026-07-21 — branch `ws3-eval-framework`)
- **Date**: 2026-07-07
- **Scope**: M2 candidate — formalizes and supersedes the fidelity/privacy portion of the
  "Mode B validation pipeline" bullet in [`docs/ROADMAP.md`](../ROADMAP.md) M2 (the
  GX/Soda structural-DQ portion of that bullet is untouched by this design — Mode A
  already owns schema/null/range/enum checks pre-write; this design does not
  duplicate them).
- **Author context**: ACTION_5 from the M1→M2 planning pass (see project memory).
- **Implemented by**: [`docs/superpowers/plans/2026-07-21-ws3-eval-framework.md`](../superpowers/plans/2026-07-21-ws3-eval-framework.md)
  (task-by-task build log) and spec §5 of
  [`docs/superpowers/specs/2026-07-20-e2e-remediation-rag-eval-evolution-design.md`](../superpowers/specs/2026-07-20-e2e-remediation-rag-eval-evolution-design.md)
  (5a/5b/5c/5d — the deltas this rewrite applies against the original design below).

## 1. Goal

Mode A (`.claude/skills/validation-mode-a.md`) answers *"is each synthetic row
schema-conformant?"* — a per-record/per-batch structural question, gated before
`WriteLanding`. It has no way to answer a different, equally important class of
question: *does the synthetic table, taken as a whole, actually look like, and
behave like, the real data it's standing in for* — and *is it accidentally leaking
real rows verbatim?* That's the gap this design closes.

Concretely, this design exists to:

1. **Quantify fidelity, privacy, and utility of synthetic vs. source, per run.**
   Fidelity = do marginal distributions, correlations, and higher-order structure
   match. Privacy = is any synthetic row a near- or exact-duplicate of a real
   row (memorization). Utility = would a model trained on the synthetic data
   perform comparably to one trained on the real data (deferred — §3 TSTR).
2. **Track engine/feature evolution over time.** Every evaluation produces one
   row in `validation_data_history`, keyed by `(run_id, engine, engine_version,
   feature_flag_tags)`. An operator can `SELECT` the metric trend for one
   `(landing_table, engine)` pair across runs — did fidelity improve when the
   embedder changed, did a new similarity default regress correlation
   preservation, did the B.1→B.2 fidelity gap close — without a dashboard,
   just SQL (per CLAUDE.md's no-Looker/no-Dataplex constraint).
3. **Be the sign-off basis for new engine features.** Today the only artifact
   for "did this change make things better or worse" is `scripts/e2e_validation_analysis.py`
   run by hand against exported CSVs (see its `_cross_overlap` docstring:
   *"a memorization proxy when the live source is not queried here"* —
   `scripts/e2e_validation_analysis.py:248-282`). This design turns that manual,
   best-effort, offline step into an automated, per-run, machine-gated BigQuery
   row computed against the *actual* live reference sample for that run.

This is explicitly **not** a replacement for Mode A. Mode A's row-level gate
stays exactly as implemented; this design adds a second, coarser-grained,
opt-in signal that Mode A structurally cannot produce (see §5 for the precise
division of labor).

## 2. Execution model

### Toggle

Two new CLI flags on `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py`'s
`parse_args()` (alongside the existing `--validation_runs_table`), spelled
with underscores — every other flag in this file's argparse already uses
underscores, and the implementation corrected this design's original
hyphenated `--enable-evaluation` to match:

```python
p.add_argument("--enable_evaluation", action="store_true", default=False,
               help="Toggle the post-WriteLanding fidelity/privacy/utility "
                    "evaluation branch (WS3): one validation_data_history row "
                    "per run + the memorization gate. Default off.")
p.add_argument("--validation_data_history_table", default="",
               help="BQ table for the evaluation row (project.dataset.validation_data_history). "
                    "Required with --enable_evaluation.")
```

`parse_args()` also adds a guard, checked alongside the existing
`--build_rag_layer`/`--rag_chunks_table` guard:

```python
if args.enable_evaluation and not args.validation_data_history_table:
    p.error("--enable_evaluation requires --validation_data_history_table")
```

**`--enable_evaluation` without the table is a launcher error** (the WS2
`--build_rag_layer` final-review precedent), **superseding this design's
original empty-skips-write contract** below — the original design had
`--validation_data_history_table` default to `""` and silently skip the write
even with the toggle on, mirroring `--validation_runs_table`'s
empty-skips-write behavior. That silent-no-op shape shipped a real defect
elsewhere in the WS2 RAG layer (`--build_rag_layer` set with no
`--rag_chunks_table` completed the whole run having written nothing, with no
operator signal), so this design's toggle intentionally does not repeat it:
turning evaluation on without giving it somewhere to land is a configuration
mistake caught at parse time, not a run that silently no-ops.

`PipelineConfig` (`packages/sdfb-beam/src/sdfb_beam/pipeline.py`) gains four
fields alongside `thresholds`/`fail_on_blocker`:

```python
enable_evaluation: bool = False
execution_id: str = ""
validation_runs_table: str = ""
validation_data_history_table: str = ""
```

`execution_id` is generated at the CLI (not left to the pipeline composer) as
`f"{args.run_id}-{uuid.uuid4().hex[:12]}"` — `run_id` stays the join key back
to `validation_runs`/`dead_letter`, while `execution_id` is the natural key
that lets a single `run_id` be re-evaluated (§4) without colliding on
`validation_data_history`'s append-only key. `validation_runs_table` is
threaded through too (not just `validation_data_history_table`) because
`EvaluationDoFn`'s previous-row lookup (§5, regression tracking) joins
through it.

`build_pipeline()` gains one new parameter, `validation_data_history_sink:
beam.PTransform | None = None`, following the exact precedent already set by
`validation_runs_sink` — both are optional sinks; the branch that uses each
is skipped entirely when its sink is `None`. `enable_evaluation` and
`validation_data_history_sink` are independent booleans (both false in a
happy-path DirectRunner unit test today; both true in a real evaluation run)
— this mirrors how `fail_on_blocker` is independent of whether
`validation_runs_sink` is even supplied.

### DAG attachment point

Post-`WriteLanding`, sibling to the existing `if validation_runs_sink is not
None:` block in `pipeline.py`'s `build_pipeline()`:

```python
if config.enable_evaluation and validation_data_history_sink is not None:
    eval_thresholds = config.thresholds or Thresholds(env="dev", blocker_failure_ratio=1.0)
    plan = choose_stratification_column(config.table_schema, reference_rows)
    cap = per_stratum_cap(max(len(plan.values), 1))
    real_sample = (
        p | "EvalReferenceRows" >> beam.Create(reference_rows)
          | "EvalSampleReference" >> beam.CombineGlobally(StratifiedReservoirFn(plan, config.run_id, cap))
    )
    synth_sample = uniq["unique"] | "EvalSampleSynthetic" >> beam.CombineGlobally(
        StratifiedReservoirFn(plan, config.run_id, cap)
    )
    eval_rows = (
        p | "EvalSeed" >> beam.Create([None])
          | "Evaluate" >> beam.ParDo(
                EvaluationDoFn(
                    table_schema=config.table_schema, run_id=config.run_id,
                    execution_id=config.execution_id or config.run_id,
                    engine=config.engine_name, engine_version=_engine_version(config.engine_name),
                    feature_flag_tags=_build_feature_flag_tags(config),
                    thresholds=eval_thresholds,
                    free_text_columns=_rag_free_text_columns(config.table_schema, reference_rows),
                    num_rows=config.num_rows, landing_table=config.landing_table,
                    history_table=config.validation_data_history_table,
                    validation_runs_table=config.validation_runs_table,
                ),
                real_sample=beam.pvalue.AsSingleton(real_sample),
                synth_sample=beam.pvalue.AsSingleton(synth_sample),
            )
    )
    _ = eval_rows | "WriteValidationDataHistory" >> validation_data_history_sink
    if config.fail_on_blocker:
        _ = eval_rows | "MemorizationGate" >> beam.ParDo(_MemorizationGateDoFn())
    result["validation_data_history"] = eval_rows
```

The sink write happens **before** the gate `ParDo` is attached — `eval_rows`
is forked to both `WriteValidationDataHistory` and (conditionally)
`MemorizationGate`, so the row always lands in BigQuery even on a run that
the gate subsequently fails. `_MemorizationGateDoFn` (§5) is wired only when
`config.fail_on_blocker` is true — the same condition that already gates
`_BlockerGateDoFn` for Mode A — so fake-client smoke/DirectRunner runs stay
informational (row written, job not failed) exactly like the Mode-A gate's
existing posture. `_rag_free_text_columns()` (already used by the WS2 RAG
population branch to pick which columns get retrieval chunks) is reused
verbatim here to source the FREE_TEXT column list for §5c's cardinality
floor, rather than duplicating B.1's `ColumnKind` classification a third
time.

Two inputs, both already materialized elsewhere in `build_pipeline()` — no new
BQ read of the full dataset:

- **Real side**: `reference_rows` — the same in-memory driver-side list already
  used to compute `digest = compute_reference_digest(reference_rows)`
  (`pipeline.py:92`), per the reference-data skill's "read reference live every
  job" contract.
- **Synthetic side**: `uniq["unique"]` — the exact PCollection already written
  to `landing_sink` (`pipeline.py:153`) and already returned as `result["valid"]`
  (`pipeline.py:174`). Evaluating this PCollection (not a duplicate generation
  path) means the evaluation branch measures precisely what landed, not what
  was merely attempted.

### Deterministic stratified sampling (seeded by `run_id`, capped ~50k rows/side)

**Stratification scheme — one concrete choice.** A new pure module,
`sdfb_core/evaluation/profile.py`, picks a single low-cardinality categorical
column to stratify on (deliberately **not** reusing `b1_rag/profile.py` or
`b2_library/fidelity.py`'s engine-local `ColumnKind` — those are explicitly
"kept local per engine during parallel development... consolidate to a shared
`engines/_fidelity.py` post-merge if duplication warrants," `b2_library/fidelity.py:9-11`,
and evaluation must not depend on whichever engine happened to run):

```python
@dataclass(frozen=True)
class StratificationPlan:
    column: str | None          # None ⇒ unstratified single "__all__" bucket
    values: tuple[object, ...]  # distinct values observed (bounds num_strata)

def choose_stratification_column(
    table_schema: TableSchema,
    reference_rows: list[dict],
    *, min_categories: int = 2, max_categories: int = 50,
) -> StratificationPlan:
    """First column (declared schema order) whose reference-sample distinct
    count falls in [min_categories, max_categories] and whose BQ type is not
    numeric. No qualifying column ⇒ StratificationPlan(column=None, values=())
    — the unstratified fallback, so this function always terminates with a
    concrete plan, never a TBD."""

def stratum_key(plan: StratificationPlan, row: dict) -> str:
    """str(row[plan.column]) if plan.column else "__all__"."""
```

Bounding `max_categories` at 50 keeps `num_strata` bounded, which bounds
per-stratum memory (see below) regardless of which table this runs against.

**Per-row deterministic priority.** `sdfb_core/evaluation/sampling.py`:

```python
_OVERALL_CAP = 50_000
_MIN_STRATUM_CAP = 1_000

def per_stratum_cap(num_strata: int, overall_cap: int = _OVERALL_CAP) -> int:
    return max(_MIN_STRATUM_CAP, overall_cap // max(num_strata, 1))

def sort_key(run_id: str, stratum: str, row: dict) -> int:
    """Deterministic 'bottom-k reservoir' priority. Reuses the same content
    digest already used for the Mode-A uniqueness gate
    (sdfb_core.validation.uniqueness.row_digest) so identical rows always
    hash identically regardless of pipeline run."""
    digest = row_digest(row)
    h = hashlib.blake2b(f"{run_id}:{stratum}:{digest}".encode(), digest_size=8)
    return int.from_bytes(h.digest(), "big")
```

Same `run_id` + same row content ⇒ same `sort_key`, every time — this is what
makes the sample **deterministic** (re-running evaluation against the same
run's data reproduces the same sample; it is *not* meant to reproduce across
different `run_id`s, since two runs legitimately see different reference pulls
per the reference-data skill's live-SELECT trade-off).

**Accumulator** (pure, in `sdfb_core/evaluation/sampling.py`). Each reservoir
entry is a `(sort_key, row_digest, row)` triple, not just `(sort_key, row)` —
`row_digest` is the tie-break, so entries stay totally ordered without ever
comparing row dicts against each other when two rows land on the same
`sort_key`:

```python
_Entry = tuple[int, str, dict]  # (sort_key, row_digest, row)

@dataclass
class ReservoirAccumulator:
    by_stratum: dict[str, list[_Entry]] = field(default_factory=dict)

def add_row(acc: ReservoirAccumulator, row: dict, *,
            plan: StratificationPlan, run_id: str, cap: int) -> ReservoirAccumulator:
    """Appends unconditionally, then trims a stratum's bucket to `cap` only
    once it reaches 2 * cap — an amortized trim, not a trim-on-every-insert,
    so a hot stratum isn't re-sorted on every single row."""

def merge_accumulators(accs: Iterable[ReservoirAccumulator], *, cap: int) -> ReservoirAccumulator:
    """Concatenates same-stratum buckets across accumulators, applying the
    same 2 * cap amortized trim as add_row — order-invariant: merging
    [acc1, acc2] and [acc2, acc1] yields the same final sample."""

def extract_sample(acc: ReservoirAccumulator, *, cap: int, overall_cap: int = _OVERALL_CAP) -> list[dict]:
    """Trims every stratum to `cap` (an unconditional final trim, since the
    2 * cap amortized trim above may have left up to 2 * cap - 1 entries in a
    bucket), flattens the union, re-sorts by (sort_key, row_digest), and
    trims to overall_cap globally. This is what guarantees the 50k/side cap
    holds even when num_strata * cap overshoots it. `cap` is an explicit,
    required keyword — extract_sample does not re-derive it from
    `per_stratum_cap`/`len(by_stratum)`, so the caller (the CombineFn below)
    is the single source of truth for which cap produced a given
    accumulator's contents."""
```

The 2×cap trim threshold (rather than trimming on every `add_row`/`merge`
call) keeps the reservoir's per-row cost O(1) amortized instead of O(cap log
cap) on every insert — the same amortized-bound shape already required of
`MergeProfilesFn`'s commutative/associative accumulator contract
(`.claude/skills/validation-mode-a.md` "Profile" section) and of
`compute_canonical_digest`'s "associative by construction" note
(`.claude/skills/reference-data.md`), both of which this reservoir also
satisfies: `add_row`/`merge_accumulators` keep memory `O(num_strata * cap)`
regardless of how many rows flow through, and merge order never changes the
final sample.

**Beam wiring** (`packages/sdfb-beam/src/sdfb_beam/dofns/evaluation.py`):

```python
class StratifiedReservoirFn(beam.CombineFn):
    def __init__(self, plan: StratificationPlan, run_id: str,
                 cap: int, overall_cap: int = 50_000): ...
    def create_accumulator(self) -> ReservoirAccumulator: ...
    def add_input(self, acc, row): ...
    def merge_accumulators(self, accs): ...
    def extract_output(self, acc) -> list[dict]: ...
```

applied as:

```python
real_sample = (
    p | "CreateReference" >> beam.Create(reference_rows)
      | "SampleReference" >> beam.CombineGlobally(StratifiedReservoirFn(plan, run_id, cap))
)
synth_sample = (
    synthetic_rows | "SampleSynthetic" >> beam.CombineGlobally(StratifiedReservoirFn(plan, run_id, cap))
)
```

`reference_rows` is already an in-memory driver-side list by the time
`build_pipeline()` runs it through `beam.Create()` — sampling it in plain
Python would be cheaper today. It's routed through the same `CombineGlobally`
as the synthetic side anyway, deliberately, for two reasons: (1) one selection
code path (and one test suite) governs both sides, so "real" and "synthetic"
samples are provably drawn by identical logic; (2) it carries forward cleanly
if `docs/ROADMAP.md`'s M2 "Reference snapshot pattern" ever replaces the
driver-side list with a genuine PCollection read — no rewrite needed at that
point, only a different upstream source into the same `CombineGlobally`.

### ONE evaluation DoFn on a single worker

`EvaluationDoFn` is constructed with every field the row needs plus the two
sample side inputs — see §2's "DAG attachment point" above for the full,
as-implemented construction (`execution_id`, `free_text_columns`, `num_rows`,
`history_table`, `validation_runs_table`, and an optional
`bq_client_factory` for injecting a fake BQ client under test, in addition to
`table_schema`/`run_id`/`engine`/`engine_version`/`feature_flag_tags`/
`thresholds`/`landing_table`). The shape below is the constant across both:

This is the identical `beam.Create([None])` + `AsSingleton` side-input shape
already used to force exactly one `_build_validation_run_row` call
(`pipeline.py:194-210`) — the same construction, reused for the same reason:
force one invocation on one bundle, i.e. one worker.

**Why heavy metrics cannot be row-level.** Every metric in §3 is a function of
the *whole* sample, not of one record: a correlation matrix, a mutual-information
matrix, an SDMetrics `QualityReport`, and a nearest-neighbor search all require
simultaneous access to every row's value for a column (or every row, for the
pairwise DCR/NNDR search) to produce a single number. Beam's `ParDo` model
gives each element an independent `process()` call with no visibility into
sibling elements — there is no way to compute `pandas.DataFrame.corr()` inside
a per-row `DoFn`. The `CombineGlobally` steps above exist precisely to
materialize the bounded sample into one bundle so a single `process()` call
can build two DataFrames and run whole-sample statistics.

**Memory bounds.** At the 50k-row/side cap: 50,000 rows × ~50 columns ×
8 bytes (float64) ≈ 20 MB per DataFrame, ≈ 40 MB for both sides — trivial for
a single non-GPU Dataflow worker. The one metric family that is *naturally*
pairwise — DCR/NNDR (§3) — is **not** computed via a dense 50k×50k pairwise
matrix (which would be ~20 GB and O(n²) memory): both use
`sklearn.neighbors.NearestNeighbors` (a tree-based nearest-neighbor index),
whose construction is `O(n log n)` and whose per-query cost is bounded — never
a materialized full pairwise distance matrix. This is a hard design
requirement, not an optimization detail: without it, this single-worker DoFn
would OOM at the target sample size.

**When the source sample is unavailable.** `extract_sample` on an empty
accumulator naturally yields `[]` (empty `reference_rows`, e.g. a
misconfigured `--reference_rows_limit=0` or a live SELECT that returned
nothing) — no exception. Likewise `synth_sample` can be empty if every
generated row was rejected pre-write (Mode-A BLOCKER gate tripped,
`uniq["unique"]` empty). `EvaluationDoFn.process()` checks
`len(real_sample) == 0 or len(synth_sample) == 0` up front and short-circuits:
it still **writes exactly one row** — `sample_rows_real`/`sample_rows_synthetic`
set to the true (possibly 0) counts, every metric column explicitly `NULL`,
`raw_metrics_json = {"status": "skipped_insufficient_sample", "reason": "..."}`
— plus a `logger.warning(...)`. The row is never silently dropped (same
"never catch-and-drop" ethos CLAUDE.md already applies to `ValidationError`
handling), and the memorization gate (§5) treats a `NULL` `identical_match_rate`
as "not evaluated," never as an implicit pass.

## 3. Metric tiers

### Tier 1 — always-on (`scipy` + `scikit-learn`, laptop-testable, no extras)

| Metric | Definition | Why tracked | Call |
|---|---|---|---|
| **KS statistic** | Max CDF distance between real/synthetic marginals, per numeric column | Standard nonparametric goodness-of-fit; catches mode collapse / distribution-shape mismatch without assuming a parametric form | `scipy.stats.ks_2samp(real_col, synth_col).statistic` |
| **Wasserstein distance** | Earth-mover's distance between the same two marginals | KS catches *shape*; Wasserstein is scale-sensitive and catches *magnitude* of the discrepancy KS can miss (e.g. a shifted-but-same-shape distribution) | `scipy.stats.wasserstein_distance(real_col, synth_col)` |
| **TVD** (categorical) | `0.5 * Σ｜p_real(c) − p_synth(c)｜` over category `c` | Bounded [0,1] measure of how far two empirical categorical distributions diverge; dependency-free (built from `value_counts(normalize=True)`) | pure numpy, no library call |
| **PSI** (drift between runs) | `Σ (this_run(c) − prev_run(c)) · ln(this_run(c) / prev_run(c))`, binned | Industry-standard drift statistic comparing *this run's* synthetic distribution to the *previous run's* (not real-vs-synthetic — see §5's regression-tracking query); PSI > 0.2 is the conventional "significant drift" threshold | pure numpy/pandas, binned via `pandas.cut`/`value_counts` |
| **JSD** | Jensen–Shannon divergence, same this-run-vs-prev-run comparison as PSI | Symmetric and bounded ([0, ln 2]) where PSI is unbounded/asymmetric — a sanity-check companion to PSI, not a replacement | `scipy.spatial.distance.jensenshannon(p, q)` |
| **Correlation-matrix diff (Frobenius)** | `‖corr_real − corr_synth‖_F` over numeric columns, Pearson *and* Spearman | Catches a synthesizer that gets every column's marginal right but destroys inter-column relationships (linear via Pearson, monotonic-rank via Spearman) — a failure KS/TVD alone cannot see | `pandas.DataFrame.corr(method="pearson"/"spearman")`, diffed via `numpy.linalg.norm(a - b, ord="fro")` |
| **Mutual-information-matrix diff (Frobenius)** | Same Frobenius-diff idea, over a pairwise MI matrix instead of a linear-correlation matrix | Catches *nonlinear* dependency loss that Pearson/Spearman (linear/monotonic only) miss | Implementation choice: every column (numeric or categorical) is first quantile-discretized (`pandas.qcut`, high-cardinality numerics only; categoricals pass through as strings), then a single `sklearn.metrics.mutual_info_score` call covers every column pair uniformly — simpler than the design's original numeric↔numeric/numeric↔categorical/categorical↔categorical three-way dispatch across `mutual_info_regression`/`mutual_info_classif`/`mutual_info_score`, at the cost of losing MI's continuous precision on numerics (acceptable for a Frobenius-diff summary statistic) |
| **DCR** (Distance to Closest Record) | For each synthetic row, the minimum Gower-style mixed distance to any real row, averaged over the synthetic sample | Low DCR ⇒ a synthetic row sits very close to some real row ⇒ memorization/near-duplication risk (the core privacy signal) | Numeric block: min-max-normalize then `sklearn.neighbors.NearestNeighbors` (Manhattan/Euclidean); categorical block: indicator mismatch; combined as an equal-weighted average per column ("Gower-style" — an approximation kept deliberately dependency-light for Tier 1's scipy/sklearn-only constraint; Tier 3's SynthEval computes the exact form). Implementation constraint: build ONE concatenated feature matrix (normalized numerics + one-hot categoricals) and query a fitted `NearestNeighbors` tree — never `metric='precomputed'`, whose dense n×n distance matrix would blow the single-worker memory bound |
| **NNDR** (Nearest-Neighbor Distance Ratio) | Per synthetic row: `dist(1st-nearest real neighbor) / dist(2nd-nearest real neighbor)`, averaged | Near 0 ⇒ one specific real record is uniquely, unambiguously the closest match ⇒ re-identification risk for *that* record; near 1 ⇒ no single record stands out. Standard SDV/anonymeter privacy-metric definition | `sklearn.neighbors.NearestNeighbors(n_neighbors=2).fit(real_matrix).kneighbors(synth_matrix)` on the same Gower-embedded space as DCR |
| **Identical-match rate** | Fraction of sampled synthetic rows whose full-row content digest exactly matches a sampled real row's digest | Direct reuse of the existing content hash (`sdfb_core.validation.uniqueness.row_digest`, already used by `EnforceUniqueness`); `0` ⇒ no verbatim leakage, `>0` ⇒ exact copy of a real row — the strongest privacy red flag, and the metric that feeds `memorization.copy_ratio` (§5) | `row_digest(synth_row) in {row_digest(r) for r in real_sample}` |
| **`column_copy_ratios`** (§5b) | Per-column fraction of non-null sampled-synthetic values that appear verbatim in the sampled-reference value set, computed only for columns whose reference-sample distinct count exceeds 100 (low-cardinality enums are legitimately, harmlessly verbatim) | Catches the "row-unique but column-verbatim" failure mode the row-level `identical_match_rate` structurally cannot see — a synthetic row can differ from every real row overall while still copying one high-cardinality column (e.g. an email or an account number) wholesale; feeds the §5b gate's `max(column_copy_ratio) >= 0.3` trip condition, stored under `raw_metrics_json.column_copy_ratios` | pure Python set membership, no library call — `sum(v in real_values for v in synth_values) / len(synth_values)` per qualifying column |
| **`cardinality_floor`** (§5c) | Per FREE_TEXT column: `landing_distinct / min(num_rows, source_distinct)` | Recorded MAJOR metric (never job-failing) exposing bounded-pool collapse in free-text generation — values well below 1.0 mean the engine drew from a small fixed pool instead of genuinely varying the text, the same class of defect a 2026-07-19 b1 run surfaced only via manual inspection | pure Python — `len({distinct synth values}) / min(num_rows, len({distinct real values}))`, `free_text_columns` sourced from the shared `_rag_free_text_columns()` helper (B.1's `ColumnKind.FREE_TEXT` classification, minus identifier-shaped columns) |

### Tier 2 — SDMetrics (primary suite; new base `sdfb-core` dependency, §6)

| Metric | Definition | Why tracked | Call |
|---|---|---|---|
| **QualityReport** | SDMetrics' composite fidelity score — aggregates column-shape (KS/TVD-like) and column-pair-trends (correlation-like) sub-scores into one 0–1 "Overall Quality Score" | The industry-recognized single number for `fidelity_overall_score` — cheaper to communicate to stakeholders than a basket of raw Tier-1 statistics, and independently validates the Tier-1 numbers rather than duplicating their exact math | `sdmetrics.reports.single_table.QualityReport().generate(real_df, synth_df, metadata).get_score()` |
| **DiagnosticReport** (incl. `NewRowSynthesis`) | SDMetrics' structural sanity report: coverage (are all categories/ranges represented), boundary adherence, and `NewRowSynthesis` — the fraction of synthetic rows that are *not* near-duplicates of any real row within SDMetrics' own numeric-tolerance definition | `NewRowSynthesis` is SDMetrics' own built-in novelty/privacy check; it uses a *tolerance band* (not exact-digest matching), so it's a useful cross-check against Tier 1's exact `identical_match_rate` rather than a replacement for it | `sdmetrics.reports.single_table.DiagnosticReport().generate(real_df, synth_df, metadata)` |

### Tier 3 — `[eval-extra]` (off by default, §6)

| Metric | Definition | Why Tier 3 | Call |
|---|---|---|---|
| **SynthEval privacy (DCR/NNDR)** | Purpose-built synthetic-tabular-data evaluation library's *native*, exact Gower-distance DCR/NNDR (not Tier 1's approximation) | Pulls its own dependency tree (reporting/plotting extras beyond plain sklearn) and is a slower, more precise re-check — appropriate as an opt-in second opinion, not an always-on gate input | `syntheval.SynthEval(real_df, synth_df).evaluate(analysis_classes=["privacy"])` |
| **Evidently drift report** | Longitudinal drift-report HTML comparing this run's synthetic distribution against a baseline (previous run, or the live reference) | Produces a durable **artifact** (an HTML file on GCS), never a dashboard — satisfies CLAUDE.md's no-Looker/no-Dataplex rule by construction (it's a file object, not a rendered service) | `evidently.Report(metrics=[DataDriftPreset()]).run(reference_data=..., current_data=...).save_html(...)`, uploaded to `gs://{bucket}/synthetic/eval/{run_id}/drift_report.html`; the GCS URI is stored in `raw_metrics_json`, never rendered in-house |

### TSTR — documented, deferred, non-priority

**Train-on-Synthetic-Test-on-Real**: fit `lightgbm.LGBMClassifier`/`LGBMRegressor`
on the synthetic sample, score F1 (classification) / RMSE (regression) against
a held-out real test split, and diff against a real-trained baseline's score
(`tstr_f1_delta`). Not implemented in this design because it needs two things
this repo doesn't yet have: (1) a heuristic for picking a "target" column on a
generic single-table schema with no declared ML task, and (2) a train/test
split protocol. The `tstr_f1_delta FLOAT64` column is reserved (`NULLABLE`,
always `NULL` today) in §4's DDL precisely so landing this later needs no
migration.

## 4. `validation_data_history` DDL

```sql
CREATE TABLE `{project}.synthetic_data_quality.validation_data_history` (
  execution_id          STRING    NOT NULL
    OPTIONS(description="Natural key for this evaluation execution (not run_id — a run_id may be re-evaluated, e.g. after a metrics-code fix, appending a new row rather than clobbering, matching this table's append-only convention)."),
  execution_timestamp   TIMESTAMP NOT NULL
    OPTIONS(description="Row write time (UTC); DAY partition key."),
  run_id                STRING    NOT NULL
    OPTIONS(description="Pipeline run id; joins to validation_runs.run_id and dead_letter.run_id."),
  engine                STRING    NOT NULL
    OPTIONS(description="b1_rag | b2_library — matches validation_runs.engine."),
  engine_version        STRING    NOT NULL
    OPTIONS(description="Engine code version (new GenerationEngine.version class attribute, proposed in §6) — distinguishes engine LOGIC evolution from the model/embedder weights already tracked via validation_runs.model_uri."),
  feature_flag_tags     ARRAY<STRING>
    OPTIONS(description="Sorted, human-diffable run-configuration tags, e.g. ['embedder:bge-small-en-v1.5', 'engine:b1_rag', 'identity_columns:customer_id', 'similarity:0.50']. See _build_feature_flag_tags in §4 notes."),
  sample_rows_real      INT64
    OPTIONS(description="Rows in the sampled real side after §2's stratified cap (0 if the reference sample was unavailable)."),
  sample_rows_synthetic INT64
    OPTIONS(description="Rows in the sampled synthetic side after §2's stratified cap (0 if every generated row was rejected pre-write)."),
  fidelity_overall_score FLOAT64
    OPTIONS(description="SDMetrics QualityReport().get_score(), 0-1. NULL when sample_rows_real or sample_rows_synthetic is 0."),
  avg_dcr               FLOAT64
    OPTIONS(description="Mean Distance to Closest Record (Tier 1, Gower-style), synthetic sample vs real sample. Lower = higher memorization risk."),
  nndr                  FLOAT64
    OPTIONS(description="Mean Nearest-Neighbor Distance Ratio (Tier 1). Near 0 = re-identification risk; near 1 = safe."),
  identical_match_rate  FLOAT64
    OPTIONS(description="Fraction of sampled synthetic rows with an exact row_digest match in the sampled real rows. Feeds the memorization.copy_ratio gate (§5)."),
  max_psi               FLOAT64
    OPTIONS(description="Max PSI across columns, this run's synthetic distribution vs the previous validation_data_history row for the same (landing_table, engine). NULL on the first run for a given pair."),
  corr_diff_frobenius   FLOAT64
    OPTIONS(description="Frobenius norm of (corr_real - corr_synth), Pearson. Spearman + the MI-matrix diff live in raw_metrics_json (not worth a dedicated column each)."),
  tstr_f1_delta         FLOAT64
    OPTIONS(description="Reserved for future TSTR (§3). Always NULL until implemented."),
  raw_metrics_json      JSON
    OPTIONS(description="Full nested metric payload: every Tier-1 per-column statistic, SDMetrics sub-scores, Tier-3 results when run, per-column binned distributions (needed by the NEXT run's PSI/JSD computation, since raw sample rows are never persisted — only these aggregates), the Evidently GCS URI when Tier 3 ran, the memorization-gate outcome, and sampling metadata (stratification column, per-stratum counts, whether the 50k cap was hit).")
)
PARTITION BY DATE(execution_timestamp)
CLUSTER BY engine, run_id;
```

Column-by-column notes not already covered inline:

- **`execution_id` vs `run_id`**: this table is append-only like `validation_runs`
  and `dead_letter` (`WRITE_APPEND` + `CREATE_NEVER`, matching the sink
  convention already used for both). `run_id` is the join key back to
  `validation_runs`/`dead_letter`; `execution_id` exists because a single
  `run_id` could in principle be re-evaluated (e.g. rerunning
  `--enable_evaluation` against already-landed data after a metrics bug fix)
  without a schema that assumes one evaluation per run. Generated at the CLI
  as `{run_id}-{uuid4hex[:12]}` (§2).
- **`engine_version`**: implemented as `GenerationEngine.version: str =
  "0.1.0"` (`sdfb_core/engines/base.py`), a class attribute both engines
  override — `B1RagEngine.version = "0.2.0"` (WS2 Phase A: pool scaling +
  per-column retrieval + ChunkStore read path) and
  `B2LibraryEngine.version = "0.2.0"` (WS1: temporal jitter, cardinality
  caps, free-text rerouting, lazy ignition) — bumped by hand when engine
  *logic* changes materially, distinct from `validation_runs.model_uri`,
  which tracks LLM *weights*, not engine code. `pipeline.py`'s
  `_engine_version(engine_name)` helper reads it off `ENGINE_REGISTRY`.
- **`feature_flag_tags`**: built by `_build_feature_flag_tags(config:
  PipelineConfig) -> list[str]` in `sdfb_beam/pipeline.py` (same file, same
  style as the existing `_build_validation_run_row` helper), reading
  `config.engine_name`, `config.similarity`, `config.strict_freetext`,
  `config.identity_columns`, `config.embedder_id`/`config.embedder_version`
  (not `embedder_uri` — the tag pins the embedder's *identity*, not its
  worker-local path), and whether `config.rag_chunks_table` is set (tagged
  `rag_read_path:on`) — sorted for determinism, so two runs with identical
  configuration produce byte-identical tag arrays (queryable via `IN
  UNNEST(feature_flag_tags)`).
- **No `landing_table`/`reference_table` column** — deliberately not
  duplicated here (per ADR 0007's DRY-across-documentation-and-code policy).
  `run_id` joins to `validation_runs`, which already carries both. §5's
  regression-tracking query does this join.
- **`raw_metrics_json` carrying per-column distributions**: this is load-bearing,
  not just a debug dump — since raw sample rows are never persisted (only
  aggregated metrics are, keeping this table small and free of duplicated PII
  beyond what's already in the landing table), the *next* run's PSI/JSD
  computation has nothing to diff against except whatever the *previous* row's
  `raw_metrics_json` stored. The evaluation DoFn must therefore write enough
  sufficient statistics (binned histograms per numeric column, frequency
  tables per categorical column) for a future run to recompute drift without
  re-reading raw rows.

Provisioning follows the exact pattern already documented for `dlq`/
`validation_runs` in `docs/DEPLOYMENT_PREREQUISITES.md` §"BigQuery — datasets &
tables": `config/bq_schema/synthetic_data_quality/validation_data_history.schema.json`
(the same BQ JSON array format as the two sibling files — created as part of
this implementation, column-for-column matching the DDL above), created with

```bash
bq mk --schema config/bq_schema/synthetic_data_quality/validation_data_history.schema.json \
      --time_partitioning_field execution_timestamp --time_partitioning_type DAY \
      --clustering_fields engine,run_id \
      project:synthetic_data_quality.validation_data_history
```

`packages/sdfb-tests/tests/unit/evaluation/test_history_schema.py` is a
drift-guard test asserting the DDL above and the schema JSON file stay in
sync — column names, types, and modes are compared field-by-field, so a
schema edit that isn't mirrored in this design doc's `CREATE TABLE` (or vice
versa) fails CI rather than silently drifting.

Adding this table (and the `--enable_evaluation`/`--validation_data_history_table`
rows) to `DEPLOYMENT_PREREQUISITES.md`'s provisioning table is a follow-on doc
change (WS5), not part of this document's scope — the same deferral pattern
the RAG-layer design used for `rag_chunks`
(`docs/designs/2026-07-07-rag-layer-design.md`).

## 5. Gate integration

### 5a. `memorization.copy_ratio` — plain BLOCKER in every env

The original design (above, kept for historical context) proposed a per-env
severity dict (`dev`/`uat`: MAJOR, `prd`: BLOCKER) plus a `resolve_severity`
resolver mirroring `blocker_failure_ratio`'s per-env *threshold* resolution.
**This was changed during implementation and the per-env shape was dropped
entirely.** As implemented, `config/thresholds.yml` carries a plain, fixed
severity:

```yaml
memorization.copy_ratio:
  dimension: privacy
  severity: BLOCKER  # spec §5a — BLOCKER in EVERY env, deliberately no per-env dict
  threshold: 0.0     # any exact sampled-row copy trips; column copy_ratio >= 0.3 also trips (§5b)
```

There is **no `resolve_severity` helper** anywhere in the implementation —
`sdfb_core/validation/thresholds.py` was not touched for this design.
`gate.py` reads `rule.get("severity", SEVERITY_BLOCKER)` as a bare string,
full stop. Two E2E cycles (2026-07-19, 2026-07-20) proved that a lenient dev
posture ships real leaks: an env-conditional severity would have let an
observed exact-copy row pass silently in `dev`, which is precisely the
failure mode this gate exists to catch. Any *observed* exact copy in the
sampled comparison is disqualifying in every env — there is no environment
where shipping a verbatim-copied real row is acceptable, so the caveat that
a sample-based ratio "genuinely warrants looser tolerance while iterating in
dev" (the original design's rationale for per-env severity) does not apply:
what's sampled is either an exact copy or it isn't, regardless of env.

### 5b. Column-level copy ratios — the compound trip condition

The row-level `identical_match_rate` alone cannot catch a synthetic row that
differs from every real row *overall* while still copying one high-cardinality
column (e.g. an account number or email) verbatim — the exact failure mode a
2026-07-20 E2E run surfaced. `EvaluationDoFn` therefore also computes
`metrics_t1.column_copy_ratios(real_sample, synth_sample)` (§3) — per-column
verbatim-copy fraction, restricted to columns whose reference-sample distinct
count exceeds 100 — and stores it in `raw_metrics_json.column_copy_ratios`.

`sdfb_core/evaluation/gate.py` (co-located with the metrics code that
produces its input, rather than folded into `validation/summary.py`'s
pre-write BLOCKER gate, since this is a distinct post-write concern) is
implemented as:

```python
class MemorizationThresholdExceeded(RuntimeError):  # noqa: N818 — mirrors BlockerThresholdExceeded
    """Fails the Dataflow job when the memorization gate trips at BLOCKER severity."""

def evaluate_memorization_gate(
    *,
    identical_match_rate: float | None,
    column_copy_ratios: dict[str, float] | None,
    thresholds: Thresholds | None,
    rule_id: str = "memorization.copy_ratio",
) -> dict:
    """Returns the outcome dict stored under raw_metrics_json.memorization_gate
    (never raises). Trips (outcome["tripped"] = True) when
    identical_match_rate > threshold OR max(column_copy_ratios.values()) >= 0.3
    — the compound §5b condition. `evaluated` is False (never a trip, never an
    implicit pass) when both inputs are None, i.e. the sample-unavailable case
    from §2."""

def raise_if_blocker(outcome: dict, *, run_id: str) -> None:
    """Separate step: raises MemorizationThresholdExceeded only when
    outcome['tripped'] and outcome['severity'] == 'BLOCKER'. Splitting this
    from evaluate_memorization_gate means the eval row is ALWAYS built and
    written before the job can be failed — the gate never prevents its own
    evidence from landing."""
```

This is a deliberate signature change from the original design's `-> None`,
single-`copy_ratio`-argument sketch: `evaluate_memorization_gate` now takes
*both* the row-level rate and the per-column ratio dict, returns a structured
outcome (not a side-effecting raise), and raising is a separate function
(`raise_if_blocker`) called from a different place in the DAG (below) than
where the outcome is computed (inside `EvaluationDoFn.process()`). A `MAJOR`
(or any non-`BLOCKER`) outcome is recorded in the written row's
`raw_metrics_json.memorization_gate` but never raises — same "MAJOR → metric
only" semantics already documented in the thresholds.yml header and
`validation-mode-a.md`'s "Failing the job" section; in practice `prd` is the
only severity value the rule ever carries today (5a), so this distinction is
latent rather than exercised, but the code path stays generic.

**Wiring**: `_MemorizationGateDoFn` (`pipeline.py`) is a new DoFn, a sibling
of `_BlockerGateDoFn`, that reads `raw_metrics_json` back off the written row
and calls `raise_if_blocker`. It is attached **downstream of the sink write**
— `eval_rows` forks to `WriteValidationDataHistory` *and*, only
`if config.fail_on_blocker`, to `MemorizationGate` — so the evaluation row is
guaranteed to have already been written by the time (if ever) the job fails.
This mirrors the existing `if config.fail_on_blocker:` gate around
`_BlockerGateDoFn` for Mode A: fake-client/DirectRunner smoke runs
(`fail_on_blocker=False`) stay informational — the gate outcome is computed
and recorded every time, but the job is only ever failed when
`fail_on_blocker` is set, which real-LLM/Dataflow runs set true.

### 5c. `column.cardinality_floor` — recorded, not gating

A second new rule, MAJOR severity, makes the b1 bounded-pool-collapse defect
(observed 2026-07-19: a FREE_TEXT column drawing from a small fixed pool
instead of genuinely varying its output) visible as a number rather than
something caught only by manual inspection:

```yaml
column.cardinality_floor:
  dimension: fidelity
  severity: MAJOR    # recorded in validation_data_history; never job-failing (§5c)
  threshold: 0.5     # landing_distinct / min(num_rows, source_distinct) per FREE_TEXT column
```

Computed by `metrics_t1.cardinality_floor(real_rows, synth_rows, *,
free_text_columns, num_rows)` (§3) — `free_text_columns` is sourced from
`_rag_free_text_columns()`, the same helper the WS2 RAG population branch
already uses to classify which columns are B.1 `ColumnKind.FREE_TEXT`
(minus identifier-shaped ones), so evaluation doesn't duplicate that
classification logic a third time. This rule is MAJOR only — there is no
code path that ever fails a job on `column.cardinality_floor`; the point is
that the ratio lands in `validation_data_history.raw_metrics_json` on every
run and can be trended, not that it gates anything.

### Regression tracking

"Previous row per `(table, engine)`" is realized inside
`EvaluationDoFn._fetch_previous_distributions()` via a join through
`validation_runs` (no duplicated `landing_table` column in
`validation_data_history`, per §4), implemented as:

```sql
SELECT h.raw_metrics_json
FROM `{history_table}` h
JOIN `{validation_runs_table}` r USING (run_id)
WHERE r.landing_table = @landing_table AND h.engine = @engine
ORDER BY h.execution_timestamp DESC
LIMIT 1
```

(only `raw_metrics_json` is selected — the caller only needs the previous
run's `distributions` sub-object for PSI/JSD, not the full row; there is no
`execution_timestamp <` upper bound in the implemented query since it always
runs *before* the current run's row is written, so there is nothing yet to
exclude). This is the **one new BQ read** this design introduces beyond the
write itself — a single `LIMIT 1` lookup, executed once inside
`EvaluationDoFn` (once per run, on the one single-worker invocation, never
per-row). Both `history_table` and `validation_runs_table` must be
non-empty for the lookup to even attempt (both are threaded from
`PipelineConfig`); any exception during the query — including a missing
table, an auth failure, or a malformed `raw_metrics_json` payload —
is caught and logged as a warning, degrading to `max_psi = NULL` for that run
rather than crashing the whole evaluation branch. The lookup serves two
purposes: (1) it supplies the previous run's binned distributions (from
`raw_metrics_json.distributions`) that Tier 1's PSI/JSD need to compute
drift-between-runs (§3); (2) more broadly, it's what "track engine/feature
evolution over time" (§1) cashes out as — any operator can run the same
join, unfiltered by `LIMIT 1` and ordered ascending, to see
`fidelity_overall_score`/`avg_dcr`/`nndr`/`corr_diff_frobenius` trend across
every run for one `(landing_table, engine)` pair, as a plain `bq query`,
never a dashboard.

### Complements — does not replace — the Mode-A DLQ gate

`EnforceUniqueness` (`sdfb_beam/dofns/uniqueness.py`) only ever compares
synthetic rows **against each other** — it dedupes within one run's batch, has
no notion of the real reference data, runs exhaustively (every row, every run,
pre-write), and its two rule_ids (`row.duplicate`, `identity.unique`) are fixed
BLOCKER severity regardless of env. `memorization.copy_ratio` fills the gap
that leaves open: it compares sampled synthetic rows **against sampled real
rows** — the one comparison Mode A structurally never makes — and runs on a
bounded sample (not exhaustive, since it's post-write and opt-in). Unlike the
original design's rationale here, severity is **not** env-conditional: it is
fixed BLOCKER everywhere (5a). The sample-based false-negative caveat (a
missed exact match that falls outside the 50k-row sample) is accepted as a
trade-off in every environment, not softened in dev/uat — because two E2E
cycles demonstrated that a lenient severity posture ships leaks, the
sampling limitation is treated as a coverage gap to shrink (a bigger sample,
a smarter stratification), never as grounds for a softer gate.

### Complements the e2e probe scripts

`scripts/e2e_validation_analysis.py`'s `_cross_overlap` (line 248) is an
offline, manually-invoked, per-column Jaccard-overlap heuristic — its own
docstring calls it *"a memorization proxy when the live source is not queried
here."* It exists because that script has no live BQ access in its intended
use (analyzing exported CSVs). The evaluation branch in this design is the
automated counterpart: it runs inside the same job that generated the data,
against the *actual* reference sample that job pulled, using exact
`row_digest` matching (not a proxy), and is machine-gated via thresholds.yml
rather than eyeballed. The e2e script remains useful for ad hoc, no-BigQuery-access
investigation; this design is what runs by default in CI/production once
`--enable_evaluation` is on.

## 6. Packaging & testability

### `sdfb-core` (pure, laptop-testable — no Beam, no GCP, no torch)

New package `packages/sdfb-core/src/sdfb_core/evaluation/`:

| Module | Contents | Import cost |
|---|---|---|
| `profile.py` | `StratificationPlan`, `choose_stratification_column`, `stratum_key` | stdlib only |
| `sampling.py` | `ReservoirAccumulator`, `sort_key`, `add_row`, `merge_accumulators`, `extract_sample`, `per_stratum_cap` | stdlib + `sdfb_core.validation.uniqueness.row_digest` |
| `metrics_t1.py` | KS/Wasserstein/TVD/PSI/JSD/correlation-diff/MI-diff/DCR/NNDR/identical-match/`column_copy_ratios`/`cardinality_floor` functions (§3 signatures) | `scipy`, `scikit-learn`, `pandas` — imported at module top (cheap, no GPU/CUDA/network — same posture as `numpy` already being an unconditional top-level import in `sdfb-core` today) |
| `metrics_t2.py` | `sdmetrics_metadata`, `coerce_for_sdmetrics`, `sdmetrics_quality`, `sdmetrics_diagnostic` | `sdmetrics` — module-top import |
| `metrics_t3.py` | `tier3_available`, `syntheval_privacy`, `evidently_drift_report` | `syntheval`/`evidently` — **deferred imports inside each function body**, `try/except ImportError` raising a clear "install `sdfb-beam[eval-extra]`" message |
| `gate.py` | `MemorizationThresholdExceeded`, `evaluate_memorization_gate`, `raise_if_blocker` | stdlib only |

**Audit resolved (2026-07-21) — sdmetrics lands as a base dependency.** The
design's original caveat — audit `sdmetrics`'s transitive tree before landing
it as a base dep, since recent releases can pull in `torch` — was resolved
during implementation: `sdmetrics>=0.28.0`'s base install (verified via `uv
sync --group dev` then `python3 -c "import scipy, sklearn, sdmetrics; import
torch"`, which raises `ModuleNotFoundError: No module named 'torch'`) pulls
only numpy/pandas/scikit-learn/scipy/copulas/tqdm/plotly — `torch` is
declared solely under `sdmetrics`'s own optional `[torch]` extra, which this
repo never installs. `sdfb-core`'s "no torch" rule holds; `sdmetrics` (and
`metrics_t2.py`) stayed base rather than moving behind `[eval-extra]`.

`sdfb-core/pyproject.toml` gains four new base dependencies, as implemented:
`scipy>=1.11.0`, `scikit-learn>=1.4.0`, `pandas>=2.2.0`, `sdmetrics>=0.28.0`.
This is a real (if narrow) widening of `sdfb-core`'s footprint — CLAUDE.md's
package map describes `sdfb-core` as "no Beam, no GCP, no torch"; none of
these four libraries are Beam, GCP, or torch, so the constraint's actual
intent (keep `sdfb-core` importable and unit-testable on a laptop with no
cloud creds, no GPU) is preserved. `pandas` is the one library here that
`sdfb-core` didn't previously depend on at all (it's an existing `sdfb-beam`
dependency); adding it to `sdfb-core` is required because the evaluation
module needs DataFrames and must itself stay Beam-free. Tier 1 + Tier 2 land
as base (non-optional) dependencies — deliberately, since the locked decision
makes them "always-on": `uv sync --group dev` alone (CLAUDE.md's documented
laptop recipe) is sufficient to unit-test every Tier 1/2 function against
fixture DataFrames, no extra flag needed.

Tier 3 (`syntheval` + `evidently`) landed as declared under the
`sdfb-beam[eval-extra]` extra (below), but `EvaluationDoFn` does **not**
call into `metrics_t3` at all — `raw["tier3"]` is unconditionally recorded
as `"not_installed"` in every evaluation row today. `metrics_t3.py`'s
functions exist for opt-in offline use and for the future `--eval_tier` knob
(§7); wiring the DoFn to call them when the extra is importable is explicitly
deferred, not merely unimplemented by oversight.

**Laptop environment note.** `scipy`/`scikit-learn` were already present in
`uv.lock` from an earlier dependency chain; `sdmetrics` is declared in
`packages/sdfb-core/pyproject.toml` but is **not yet reflected in
`uv.lock`** — the lockfile regen (`uv lock`) is deferred to the M4 machine
(the one with real GCP/GPU access, per this repo's hardware split), and the
tests in this branch were run against a local `sdmetrics` install pulled
directly from pypi.org rather than a synced lockfile. This is a known,
intentional gap, not an oversight — `uv.lock` will be regenerated on M4 as
part of the next E2E prep pass.

`sdfb-beam/pyproject.toml` gains one new optional-dependency group, following
the exact precedent already set by `gpu`/`embedding`/`library` (extras
declared on `sdfb-beam` even though the importing code lives in `sdfb-core` —
`sdfb-beam/pyproject.toml:27-55`):

```toml
# Tier-3 evaluation extras — SynthEval + Evidently. Off by default; the
# functions that import these are deferred-import (sdfb_core/evaluation/metrics_t3.py)
# so their absence never breaks a Tier 1/2 evaluation run.
eval-extra = [
    "syntheval>=1.5.0",
    "evidently>=0.4.0",
]
```

`uv sync --group dev --package sdfb-beam --extra eval-extra` opts into Tier 3
on request; it is never required for Tier 1/2 or for the default `pytest`
baseline.

### `sdfb-beam` (Beam wiring only)

New module `packages/sdfb-beam/src/sdfb_beam/dofns/evaluation.py`:
`StratifiedReservoirFn` (a thin `beam.CombineFn` wrapping
`sdfb_core.evaluation.sampling`'s pure functions) and `EvaluationDoFn` (a
`beam.DoFn` whose `process()` calls into `sdfb_core.evaluation.metrics_t1`
and `metrics_t2`, plus `evaluate_memorization_gate` from `gate.py`). As
implemented, `process()` never calls `metrics_t3` — `raw["tier3"]` is
unconditionally recorded as `"not_installed"` (§6 audit note above); Tier 3
stays reachable only via direct, offline calls into `metrics_t3`'s functions.
This module is still the **only** place any of Tier 1/2's libraries are
imported at Beam-graph-construction or worker-runtime; `pipeline.py` itself
imports only `raise_if_blocker`, `choose_stratification_column`, and
`per_stratum_cap` from `sdfb_core.evaluation`, plus the `EvaluationDoFn`/
`StratifiedReservoirFn` classes from `sdfb_beam.dofns` — never
`scipy`/`sdmetrics`/etc. directly — matching how
`PanderaValidateBatchDoFn`/`ValidateRecordDoFn` are the only importers of
`pandera` today.

### Tests

`packages/sdfb-tests/tests/unit/evaluation/` (mirrors the existing
`tests/unit/dofns/`, `tests/unit/engines/` layout), as implemented:

- `test_profile.py`, `test_sampling.py`, `test_metrics_t1.py` (marginals +
  drift), `test_metrics_t1_structure.py` (correlation/MI), `test_metrics_t1_privacy.py`
  (DCR/NNDR, identical-match, column copy ratios, cardinality floor),
  `test_metrics_t2.py`, `test_gate.py` — small, hand-built fixture
  DataFrames/row-lists (≤ 20 rows is enough to exercise every formula), no
  Beam required. `test_sampling.py` specifically asserts (a) determinism —
  same `run_id` + same rows fed twice ⇒ identical sample, (b) cap
  enforcement — output never exceeds `per_stratum_cap`/50k regardless of
  input size, and (c) merge-order invariance — by calling
  `create_accumulator`/`add_row`/`merge_accumulators`/`extract_sample`
  directly as plain functions, the same unit-testing approach
  `MergeProfilesFn`'s commutativity requirement already implies for the
  whylogs profile merge.
- `test_metrics_t3.py` calls `metrics_t3.tier3_available()` to detect
  whether `eval-extra` is installed: when absent (the laptop default), it
  asserts `syntheval_privacy`/`evidently_drift_report` raise `ImportError`
  matching `"eval-extra"`; when present, `pytest.importorskip` gates the
  "runs when installed" assertions. Neither path needs a new pytest marker
  wired into CLAUDE.md's documented `pytest -m "not gpu and not gcp"`
  baseline — deliberately minimizing this design's footprint on the
  existing verify recipe.
- `test_history_schema.py` — the Task-12 drift guard (§4) diffing the DDL's
  column set against `validation_data_history.schema.json`.
- Beam-side tests (`tests/unit/dofns/test_evaluation.py`,
  `tests/unit/test_pipeline_evaluation.py`) exercise
  `StratifiedReservoirFn`/`EvaluationDoFn`/the `enable_evaluation` DAG branch
  and `_MemorizationGateDoFn` via `TestPipeline`/`DirectRunner`, same pattern
  as the existing DoFn/pipeline test suites.

## 7. Out of scope / future

- **TSTR** (§3): target-column selection heuristic, train/test split
  convention, LightGBM baseline management. `tstr_f1_delta` is reserved
  (`NULLABLE`) in §4's DDL so landing it later needs no migration.
- **Multi-table fidelity** (FK-aware joint distributions, cross-table
  correlation): out of scope per CLAUDE.md's single-table-only M1/M2-so-far
  constraint; `validation_data_history` is single-table-scoped exactly like
  `validation_runs`.
- **Dashboards**: explicitly not proposed. Any human-facing view of
  `validation_data_history` is a `bq query` (§5's regression-tracking query)
  or a GCS HTML artifact (Tier 3 Evidently) — never Looker, Dataplex, or any
  managed dashboarding service.
- Also flagged, not required for this design to be complete: a `--eval_tier`
  CLI knob to gate Tier 3 execution behind an explicit request rather than
  only extra-availability; a cheaper `--eval_privacy_only` fast-path (skip
  Tier 1 marginal stats, run only DCR/NNDR/identical-match) for repeated
  privacy-only checks; and `engine_version` migration tooling if that
  attribute's semantics change. Natural next increments, not blockers here.
