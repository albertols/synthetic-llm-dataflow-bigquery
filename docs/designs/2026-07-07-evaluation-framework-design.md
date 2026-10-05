# Design — Evaluation framework (`synthetic_data_quality.validation_data_history`)

> **Status: PARTIALLY IMPLEMENTED** (proposed 2026-07-07; Tier-1/2/3 code lives on branch `ws3-eval-framework` — `sdfb_core/evaluation/` — merge pending)
> · visuals retrofitted 2026-08-05 per the `visual-first-documentation` skill
> · related: [ADR 0022](../adr/0022-stats-driven-generation.md) (the
> source-side stats this framework's landing-side metrics mirror),
> [`2026-08-05-source-table-stats.md`](2026-08-05-source-table-stats.md)
> (entropy/decile concept figures — the same mathematics, source side).
> Concept figures regenerate via
> `uv run --no-sync python3 scripts/doc/make_eval_figures.py`.

- **Scope**: formalizes and supersedes the fidelity/privacy portion of the
  "Mode B validation pipeline" bullet in [`docs/ROADMAP.md`](../ROADMAP.md) M2 (the
  GX/Soda structural-DQ portion of that bullet is untouched by this design — Mode A
  already owns schema/null/range/enum checks pre-write; this design does not
  duplicate them).
- **Author context**: ACTION_5 from the M1→M2 planning pass.

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
   for "did this change make things better or worse" is `scripts/e2e/e2e_validation_analysis.py`
   run by hand against exported CSVs (see its `_cross_overlap` docstring:
   *"a memorization proxy when the live source is not queried here"* —
   `scripts/e2e/e2e_validation_analysis.py:248-282`). This design turns that manual,
   best-effort, offline step into an automated, per-run, machine-gated BigQuery
   row computed against the *actual* live reference sample for that run.

This is explicitly **not** a replacement for Mode A. Mode A's row-level gate
stays exactly as implemented; this design adds a second, coarser-grained,
opt-in signal that Mode A structurally cannot produce (see §5 for the precise
division of labor).

### Where the branch sits

Claim: *evaluation is a post-`WriteLanding` sibling branch — it measures what
landed, never gates what is being written (Mode A already owns that).*

```mermaid
flowchart LR
  classDef beam  fill:#eb6834,color:#fff,stroke:#b44f26
  classDef cpu   fill:#1baf7a,color:#fff,stroke:#127a55
  classDef store fill:#2a78d6,color:#fff,stroke:#1d5599
  classDef data  fill:#6b7280,color:#fff,stroke:#4b5563

  GEN["🔀 GenerateRecordsDoFn"]:::beam --> MODEA["🛡️ Mode A gate<br/>schema · nulls · uniqueness"]:::cpu
  MODEA --> WL["🔀 WriteLanding<br/>FILE_LOADS"]:::beam
  MODEA -. rejected rows .-> DLQ[("🗄️ dead_letter")]:::store
  WL --> LAND[("🗄️ landing table")]:::store
  subgraph eval ["--enable-evaluation branch (post-write, opt-in)"]
    SREAL["🔀 SampleReference<br/>CombineGlobally reservoir"]:::beam
    SSYN["🔀 SampleSynthetic<br/>CombineGlobally reservoir"]:::beam
    EV["🔀 EvaluationDoFn<br/>one worker, whole-sample stats"]:::beam
    GATE["🛡️ memorization gate<br/>BLOCKER only in prd"]:::cpu
    SREAL --> EV
    SSYN --> EV
    EV --> GATE
  end
  REF["⚪ reference_rows<br/>driver-side list"]:::data --> SREAL
  MODEA -- "uniq['unique'] (what landed)" --> SSYN
  EV --> HIST[("🗄️ validation_data_history")]:::store
```

The two inputs are already materialized elsewhere in the DAG (no new full BQ
read); the single-worker `EvaluationDoFn` exists because every §3 metric is a
whole-sample function — details in the sections below.

## 2. Execution model

### Toggle

A new CLI flag on `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py`'s
`parse_args()` (alongside the existing `--validation_runs_table`,
`run_pipeline.py:88-90`):

```python
p.add_argument("--enable-evaluation", action="store_true", default=False,
               help="Toggle the post-WriteLanding fidelity/privacy/utility "
                    "evaluation branch. Independent of --validation_runs_table "
                    "and of the Mode-A --fail_on_blocker gate. Default off.")
p.add_argument("--validation_data_history_table", default="",
               help="BQ table for the evaluation row (project.dataset.table); "
                    "empty skips the write even when --enable-evaluation is set "
                    "(mirrors --validation_runs_table's empty-skips-write contract).")
```

`PipelineConfig` (`packages/sdfb-beam/src/sdfb_beam/pipeline.py:45-74`) gains two
fields alongside `thresholds`/`fail_on_blocker`:

```python
enable_evaluation: bool = False
```

`build_pipeline()` gains one new parameter, `validation_data_history_sink:
beam.PTransform | None = None`, following the exact precedent already set by
`validation_runs_sink` (`pipeline.py:84`) — both are optional sinks; the branch
that uses each is skipped entirely when its sink is `None`. `enable_evaluation`
and `validation_data_history_sink` are independent booleans (both false in a
happy-path DirectRunner unit test today; both true in a real evaluation run) —
this mirrors how `fail_on_blocker` is independent of whether
`validation_runs_sink` is even supplied.

### DAG attachment point

Post-`WriteLanding`, sibling to the existing `if validation_runs_sink is not
None:` block (`pipeline.py:181-215`) — a new block:

```python
if config.enable_evaluation and validation_data_history_sink is not None:
    eval_row = _build_evaluation_branch(
        p, reference_rows=reference_rows, synthetic_rows=uniq["unique"],
        config=config, thresholds=thresholds,
    )
    _ = eval_row | "WriteValidationDataHistory" >> validation_data_history_sink
    result["validation_data_history"] = eval_row
```

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

**Accumulator** (pure, in `sdfb_core/evaluation/sampling.py`):

```python
@dataclass
class ReservoirAccumulator:
    by_stratum: dict[str, list[tuple[int, dict]]]  # bounded bottom-k per stratum

def add_row(acc: ReservoirAccumulator, row: dict, *,
            plan: StratificationPlan, run_id: str, cap: int) -> ReservoirAccumulator: ...
def merge_accumulators(accs: Iterable[ReservoirAccumulator], *, cap: int) -> ReservoirAccumulator: ...
def extract_sample(acc: ReservoirAccumulator, *, overall_cap: int = _OVERALL_CAP) -> list[dict]:
    """Flattens every stratum's bottom-k, then re-sorts the union by sort_key
    and trims to overall_cap globally. This is what guarantees the 50k/side
    cap holds even when num_strata * per_stratum_cap overshoots it."""
```

`add_row`/`merge_accumulators` keep each stratum's list bounded to `cap`
entries (smallest `sort_key` wins — a bounded max-heap, not an unbounded list),
so accumulator memory is `O(num_strata * per_stratum_cap)` regardless of how
many rows flow through — this is the same commutative/associative-accumulator
shape already required of `MergeProfilesFn` in the whylogs merge
(`.claude/skills/validation-mode-a.md` "Profile" section) and of
`compute_canonical_digest`'s "associative by construction" note
(`.claude/skills/reference-data.md`).

The sampling mechanism, end to end — deterministic bottom-k per stratum,
then a global re-trim:

```mermaid
flowchart LR
  classDef cpu  fill:#1baf7a,color:#fff,stroke:#127a55
  classDef data fill:#6b7280,color:#fff,stroke:#4b5563
  ROWS["⚪ rows (either side)"]:::data --> KEY["⚙️ stratum_key<br/>one categorical column"]:::cpu
  KEY --> PRI["🎲 sort_key = blake2b<br/>(run_id : stratum : row_digest)"]:::cpu
  PRI --> HEAP["⚙️ bounded bottom-k<br/>per stratum (max-heap)"]:::cpu
  HEAP --> UNION["⚙️ union → re-sort<br/>→ trim to 50k"]:::cpu
  UNION --> OUT["⚪ deterministic sample<br/>same run_id ⇒ same rows"]:::data
```

The hash priority is what makes this a *deterministic* reservoir: bottom-k
by a content-keyed hash is a uniform random sample for any fixed `run_id`
(each row's priority is an i.i.d. 64-bit value), yet re-running evaluation
for the same run reproduces it bit-for-bit — no RNG state to persist.

**Beam wiring** (`packages/sdfb-beam/src/sdfb_beam/dofns/evaluation.py`):

```python
class StratifiedReservoirFn(beam.CombineFn):
    def __init__(self, plan: StratificationPlan, run_id: str,
                 per_stratum_cap: int, overall_cap: int = 50_000): ...
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

```python
eval_row = (
    p | "EvalSeed" >> beam.Create([None])
      | "Evaluate" >> beam.ParDo(
            EvaluationDoFn(table_schema=config.table_schema, run_id=config.run_id,
                           engine=config.engine_name, engine_version=..., 
                           feature_flag_tags=_build_feature_flag_tags(config),
                           landing_table=config.landing_table, thresholds=thresholds),
            real_sample=beam.pvalue.AsSingleton(real_sample),
            synth_sample=beam.pvalue.AsSingleton(synth_sample),
        )
)
```

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

### Why the fidelity family needs BOTH a sup-statistic and a mass-statistic

**Claim: two failure modes with the same Wasserstein distance can differ 5×
in KS — the two statistics see different failures, so the framework tracks
both.**

![KS vs Wasserstein: same W1, 5x different KS](assets/eval-ks-vs-wasserstein.png)

*Entry level:* both panels compare a real CDF (blue) to a synthetic one
(orange). KS is the tallest **vertical gap** between the curves (the black
bar); Wasserstein-1 is the **entire shaded area** between them. A shifted
twin moves every value a little (big gap, small-per-value area); a tail
escape moves 5% of values a long way (tiny gap — only 5% of mass is ever
displaced at any x — but the same total area). One number stays at 5.0 in
both panels; the other changes 5×.

*Research level:* `KS = supₓ|F(x) − G(x)|` (two-sample
Kolmogorov–Smirnov, [`scipy.stats.ks_2samp`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.ks_2samp.html));
`W₁ = ∫|F(x) − G(x)|dx` — for one-dimensional marginals the earth-mover
distance *is* the area between the CDFs
([`scipy.stats.wasserstein_distance`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.wasserstein_distance.html)),
which is why both read off the same picture. A sampler that clamps the tail
(e.g. inverse-CDF's p90→p100 linearization, see the
[source-stats doc](2026-08-05-source-table-stats.md)) shows up in W₁ long
before KS notices.

### Tier 1 — always-on (`scipy` + `scikit-learn`, laptop-testable, no extras)

| Metric | Definition | Why tracked | Call |
|---|---|---|---|
| **KS statistic** | Max CDF distance between real/synthetic marginals, per numeric column | Standard nonparametric goodness-of-fit; catches mode collapse / distribution-shape mismatch without assuming a parametric form | `scipy.stats.ks_2samp(real_col, synth_col).statistic` |
| **Wasserstein distance** | Earth-mover's distance between the same two marginals | KS catches *shape*; Wasserstein is scale-sensitive and catches *magnitude* of the discrepancy KS can miss (e.g. a shifted-but-same-shape distribution) | `scipy.stats.wasserstein_distance(real_col, synth_col)` |
| **TVD** (categorical) | `0.5 * Σ｜p_real(c) − p_synth(c)｜` over category `c` | Bounded [0,1] measure of how far two empirical categorical distributions diverge; dependency-free (built from `value_counts(normalize=True)`) | pure numpy, no library call |
| **PSI** (drift between runs) | `Σ (this_run(c) − prev_run(c)) · ln(this_run(c) / prev_run(c))`, binned | Industry-standard drift statistic comparing *this run's* synthetic distribution to the *previous run's* (not real-vs-synthetic — see §5's regression-tracking query); PSI > 0.2 is the conventional "significant drift" threshold | pure numpy/pandas, binned via `pandas.cut`/`value_counts` |
| **JSD** | Jensen–Shannon divergence, same this-run-vs-prev-run comparison as PSI | Symmetric and bounded ([0, ln 2]) where PSI is unbounded/asymmetric — a sanity-check companion to PSI, not a replacement | `scipy.spatial.distance.jensenshannon(p, q)` |
| **Correlation-matrix diff (Frobenius)** | `‖corr_real − corr_synth‖_F` over numeric columns, Pearson *and* Spearman | Catches a synthesizer that gets every column's marginal right but destroys inter-column relationships (linear via Pearson, monotonic-rank via Spearman) — a failure KS/TVD alone cannot see | `pandas.DataFrame.corr(method="pearson"/"spearman")`, diffed via `numpy.linalg.norm(a - b, ord="fro")` |
| **Mutual-information-matrix diff (Frobenius)** | Same Frobenius-diff idea, over a pairwise MI matrix instead of a linear-correlation matrix | Catches *nonlinear* dependency loss that Pearson/Spearman (linear/monotonic only) miss | `sklearn.feature_selection.mutual_info_regression`/`mutual_info_classif` (numeric↔numeric / numeric↔categorical), `sklearn.metrics.mutual_info_score` (categorical↔categorical), assembled into a matrix and Frobenius-diffed the same way |
| **DCR** (Distance to Closest Record) | For each synthetic row, the minimum Gower-style mixed distance to any real row, averaged over the synthetic sample | Low DCR ⇒ a synthetic row sits very close to some real row ⇒ memorization/near-duplication risk (the core privacy signal) | Numeric block: min-max-normalize then `sklearn.neighbors.NearestNeighbors` (Manhattan/Euclidean); categorical block: indicator mismatch; combined as an equal-weighted average per column ("Gower-style" — an approximation kept deliberately dependency-light for Tier 1's scipy/sklearn-only constraint; Tier 3's SynthEval computes the exact form). Implementation constraint: build ONE concatenated feature matrix (normalized numerics + one-hot categoricals) and query a fitted `NearestNeighbors` tree — never `metric='precomputed'`, whose dense n×n distance matrix would blow the single-worker memory bound |
| **NNDR** (Nearest-Neighbor Distance Ratio) | Per synthetic row: `dist(1st-nearest real neighbor) / dist(2nd-nearest real neighbor)`, averaged | Near 0 ⇒ one specific real record is uniquely, unambiguously the closest match ⇒ re-identification risk for *that* record; near 1 ⇒ no single record stands out. Standard SDV/anonymeter privacy-metric definition | `sklearn.neighbors.NearestNeighbors(n_neighbors=2).fit(real_matrix).kneighbors(synth_matrix)` on the same Gower-embedded space as DCR |
| **Identical-match rate** | Fraction of sampled synthetic rows whose full-row content digest exactly matches a sampled real row's digest | Direct reuse of the existing content hash (`sdfb_core.validation.uniqueness.row_digest`, already used by `EnforceUniqueness`); `0` ⇒ no verbatim leakage, `>0` ⇒ exact copy of a real row — the strongest privacy red flag, and the metric that feeds `memorization.copy_ratio` (§5) | `row_digest(synth_row) in {row_digest(r) for r in real_sample}` |

### The privacy pair, geometrically

**Claim: DCR flags a synthetic row parked on a real record; NNDR flags a
row for which ONE real record is unambiguously closest — different privacy
failures, one embedded space.**

![DCR and NNDR geometry over the Gower-embedded space](assets/eval-dcr-nndr.png)

*Entry level:* left panel — the orange × sits on top of a real row: its
distance to the closest record is ~0, the memorization signal. Right
panel — the orange × is not on any real row, but its nearest real neighbor
(solid line) is far closer than its second-nearest (dashed): whoever that
one record belongs to is singled out. The aqua × is safe on both readings:
comfortably distant, and ambiguous between neighbors.

*Research level:* both metrics live in the Gower-style mixed-feature
embedding ([Gower 1971](https://doi.org/10.2307/2528823)): min-max-normalized
numerics + one-hot categoricals. `DCR = min_r d(s, r)`;
`NNDR = d₍₁₎/d₍₂₎ ∈ (0, 1]` — the standard SDV/anonymeter definitions
([Giomi et al. 2022](https://arxiv.org/abs/2211.10459)). The §2 memory
bound is why the design mandates a fitted
`sklearn.neighbors.NearestNeighbors` tree (`O(n log n)` build, bounded
per-query) and forbids the dense 50k×50k distance matrix (~20 GB).
`identical_match_rate` is the degenerate DCR=0 case caught exactly, via the
same `row_digest` used by `EnforceUniqueness` — memorization risk made
gate-able ([Carlini et al. 2021](https://arxiv.org/abs/2012.07805)).

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
  convention already used for both — `run_pipeline.py:252-271`). `run_id` is
  the join key back to `validation_runs`/`dead_letter`; `execution_id` exists
  because a single `run_id` could in principle be re-evaluated (e.g. rerunning
  `--enable-evaluation` against already-landed data after a metrics bug fix)
  without a schema that assumes one evaluation per run.
- **`engine_version`**: no such attribute exists on `GenerationEngine` today —
  both `B1RagEngine` and `B2LibraryEngine` declare only `name`
  (`b1_rag/engine.py:75`, `b2_library/engine.py:59`). This design proposes
  adding a parallel `version: str = "0.1.0"` class attribute, bumped by hand
  when engine *logic* changes materially — distinct from
  `validation_runs.model_uri`, which tracks LLM *weights*, not engine code.
- **`feature_flag_tags`**: built by a new `_build_feature_flag_tags(config:
  PipelineConfig) -> list[str]` function in `sdfb_beam/pipeline.py` (same file,
  same style as the existing `_build_validation_run_row` helper), reading
  `config.engine_name`, `config.similarity`, `config.identity_columns`,
  `config.embedder_uri` — sorted for determinism, so two runs with identical
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
tables": a new `config/bq_schema/synthetic_data_quality/validation_data_history.schema.json`
(the same BQ JSON array format as the two sibling files), created with

```bash
bq mk --schema config/bq_schema/synthetic_data_quality/validation_data_history.schema.json \
      --time_partitioning_field execution_timestamp --time_partitioning_type DAY \
      --clustering_fields engine,run_id \
      project:synthetic_data_quality.validation_data_history
```

Adding this table (and the `--enable-evaluation`/`--validation_data_history_table`
rows) to `DEPLOYMENT_PREREQUISITES.md`'s provisioning table is a follow-on doc
change when this design is implemented, not part of this document's scope —
the same deferral pattern the RAG-layer design used for `rag_chunks`
(`docs/designs/2026-07-07-rag-layer-design.md:290-293`).

## 5. Gate integration

### New rule: `memorization.copy_ratio`

`config/thresholds.yml` gains a new rule whose **severity itself varies by
env** (every existing rule has a fixed severity and, at most, a per-env
*threshold*; this is the first rule where severity is per-env, since a
sample-based ratio genuinely warrants looser tolerance while iterating in dev
than at prd sign-off):

```yaml
memorization.copy_ratio:
  dimension: privacy
  severity:
    dev: MAJOR
    uat: MAJOR
    prd: BLOCKER
  threshold: 0.0   # any exact duplicate of a sampled real row is a violation
```

`Thresholds.rules` (`sdfb_core/validation/thresholds.py:29`) already stores the
raw per-rule dict verbatim (`rules: dict[str, dict] = Field(default_factory=dict)`)
— no Pydantic model change is required to hold this new rule shape. What's new
is a small resolver, added to the same module:

```python
def resolve_severity(thresholds: Thresholds, rule_id: str, *, default: str = "MINOR") -> str:
    """Mirrors the per-env resolution already used for blocker_failure_ratio
    (Thresholds.from_mapping, thresholds.py:33-34): severity may be a bare
    string (fixed, like every existing rule) or a per-env dict (new, for
    memorization.copy_ratio)."""
    raw = thresholds.rules.get(rule_id, {}).get("severity", default)
    return raw.get(thresholds.env, default) if isinstance(raw, dict) else raw
```

And a new pure module, `sdfb_core/evaluation/gate.py` (co-located with the
metrics code that produces its input, rather than folded into
`validation/summary.py`'s pre-write BLOCKER gate, since this is a distinct
post-write concern):

```python
class MemorizationThresholdExceeded(RuntimeError):  # noqa: N818 — mirrors BlockerThresholdExceeded
    """Raised to FAIL the Dataflow job when the memorization gate trips at BLOCKER severity."""

def evaluate_memorization_gate(
    copy_ratio: float | None, thresholds: Thresholds,
    *, rule_id: str = "memorization.copy_ratio",
) -> None:
    """No-ops when copy_ratio is None (§2's 'sample unavailable' case — gate
    not evaluated, never treated as an implicit pass). Raises
    MemorizationThresholdExceeded when copy_ratio exceeds the rule's threshold
    AND resolve_severity(...) == 'BLOCKER' for this env. A MAJOR-severity
    breach is recorded (identical_match_rate is already in the written row)
    but does not fail the job — same 'MAJOR → metric only' semantics already
    documented in the thresholds.yml header and validation-mode-a.md's
    'Failing the job' section."""
```

Wired in `pipeline.py` as a sibling to `_BlockerGateDoFn`
(`pipeline.py:249-259`) — a new `_MemorizationGateDoFn` inside the
`enable_evaluation` branch, unconditional on any extra CLI flag: the
thresholds.yml row's per-env severity *is* the toggle (BLOCKER only in `prd`),
so no new "should this fail the job" knob is needed beyond what's already
resolved from `--env`.

### Regression tracking

"Previous row per `(table, engine)`" is realized via a join through
`validation_runs` (no duplicated `landing_table` column in
`validation_data_history`, per §4):

```sql
SELECT h.*
FROM `{project}.synthetic_data_quality.validation_data_history` h
JOIN `{project}.synthetic_data_quality.validation_runs` r USING (run_id)
WHERE r.landing_table = @landing_table
  AND h.engine = @engine
  AND h.execution_timestamp < @this_execution_timestamp
ORDER BY h.execution_timestamp DESC
LIMIT 1;
```

This is the **one new BQ read** this design introduces beyond the write
itself — a single `LIMIT 1` lookup, executed once inside `EvaluationDoFn`
(once per run, on the one single-worker invocation, never per-row — consistent
with "heavy metrics must not run in row-level DoFns," since this isn't
row-level at all). It serves two purposes: (1) it supplies the previous run's
binned distributions (from `raw_metrics_json`) that Tier 1's PSI/JSD need to
compute drift-between-runs (§3); (2) more broadly, it's what "track
engine/feature evolution over time" (§1) cashes out as — any operator can run
the same join, unfiltered by `LIMIT 1` and ordered ascending, to see
`fidelity_overall_score`/`avg_dcr`/`nndr`/`corr_diff_frobenius` trend across
every run for one `(landing_table, engine)` pair, as a plain `bq query`, never
a dashboard.

### Complements — does not replace — the Mode-A DLQ gate

`EnforceUniqueness` (`sdfb_beam/dofns/uniqueness.py`) only ever compares
synthetic rows **against each other** — it dedupes within one run's batch, has
no notion of the real reference data, runs exhaustively (every row, every run,
pre-write), and its two rule_ids (`row.duplicate`, `identity.unique`) are fixed
BLOCKER severity regardless of env. `memorization.copy_ratio` fills the gap
that leaves open: it compares sampled synthetic rows **against sampled real
rows** — the one comparison Mode A structurally never makes — runs on a bounded
sample (not exhaustive, since it's post-write and opt-in), and its severity is
env-conditional because a sample-based ratio can have false negatives (a
missed exact match outside the sample) that make a fixed always-BLOCKER
posture too strict while iterating in dev.

### Complements the e2e probe scripts

`scripts/e2e/e2e_validation_analysis.py`'s `_cross_overlap` (line 248) is an
offline, manually-invoked, per-column Jaccard-overlap heuristic — its own
docstring calls it *"a memorization proxy when the live source is not queried
here."* It exists because that script has no live BQ access in its intended
use (analyzing exported CSVs). The evaluation branch in this design is the
automated counterpart: it runs inside the same job that generated the data,
against the *actual* reference sample that job pulled, using exact
`row_digest` matching (not a proxy), and is machine-gated via thresholds.yml
rather than eyeballed. The e2e script remains useful for ad hoc, no-BigQuery-access
investigation; this design is what runs by default in CI/production once
`--enable-evaluation` is on.

## 6. Packaging & testability

### `sdfb-core` (pure, laptop-testable — no Beam, no GCP, no torch)

New package `packages/sdfb-core/src/sdfb_core/evaluation/`:

| Module | Contents | Import cost |
|---|---|---|
| `profile.py` | `StratificationPlan`, `choose_stratification_column`, `stratum_key` | stdlib only |
| `sampling.py` | `ReservoirAccumulator`, `sort_key`, `add_row`, `merge_accumulators`, `extract_sample`, `per_stratum_cap` | stdlib + `sdfb_core.validation.uniqueness.row_digest` |
| `metrics_t1.py` | KS/Wasserstein/TVD/PSI/JSD/correlation-diff/MI-diff/DCR/NNDR/identical-match functions (§3 signatures) | `scipy`, `scikit-learn`, `pandas` — imported at module top (cheap, no GPU/CUDA/network — same posture as `numpy` already being an unconditional top-level import in `sdfb-core` today) |
| `metrics_t2.py` | `sdmetrics_quality_score`, `sdmetrics_diagnostic` | `sdmetrics` — module-top import |
| `metrics_t3.py` | `syntheval_privacy`, `evidently_drift_report` | `syntheval`/`evidently` — **deferred imports inside each function body**, `try/except ImportError` raising a clear "install `sdfb-beam[eval-extra]`" message |
| `gate.py` | `MemorizationThresholdExceeded`, `evaluate_memorization_gate` | stdlib only |

**Caveat — audit before implementing.** Before landing `sdmetrics` as a base
dependency, audit its transitive dependency tree for the version pinned here
(`>=0.16.0`): recent `sdmetrics` releases can pull in `torch` transitively,
which would violate `sdfb-core`'s "no torch" rule (CLAUDE.md package map) the
same way this whole paragraph argues the other three libraries don't. If the
audited version does pull `torch`, move `sdmetrics` (and `metrics_t2.py`)
behind the `[eval-extra]` tier alongside Tier 3 instead of base — do not land
it as a base dependency in that case.

`sdfb-core/pyproject.toml` gains four new base dependencies:
`scipy>=1.11.0`, `scikit-learn>=1.4.0`, `sdmetrics>=0.16.0`, `pandas>=2.2.0`.
This is a real (if narrow) widening of `sdfb-core`'s footprint — CLAUDE.md's
package map describes `sdfb-core` as "no Beam, no GCP, no torch"; none of
these four libraries are Beam, GCP, or torch, so the constraint's actual
intent (keep `sdfb-core` importable and unit-testable on a laptop with no
cloud creds, no GPU) is preserved. `pandas` is the one library here that
`sdfb-core` didn't previously depend on at all (it's an existing `sdfb-beam`
dependency, `sdfb-beam/pyproject.toml:19`); adding it to `sdfb-core` is
required because the evaluation module needs DataFrames and must itself stay
Beam-free. Tier 1 + Tier 2 land as base (non-optional) dependencies —
deliberately, since the locked decision makes them "always-on": `uv sync
--group dev` alone (CLAUDE.md's documented laptop recipe) is sufficient to
unit-test every Tier 1/2 function against fixture DataFrames, no extra flag
needed.

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
`beam.DoFn` whose `process()` calls into `sdfb_core.evaluation.metrics_t1`/
`metrics_t2`/`gate`, and — only when the Tier-3 extra is importable —
`metrics_t3`). This is the **only** place any of Tier 1–3's libraries are
imported at Beam-graph-construction or worker-runtime; `pipeline.py` itself
imports only `_build_feature_flag_tags` and the `EvaluationDoFn`/
`StratifiedReservoirFn` classes, never `scipy`/`sdmetrics`/etc. directly —
matching how `PanderaValidateBatchDoFn`/`ValidateRecordDoFn` are the only
importers of `pandera` today.

### Tests

New `packages/sdfb-tests/tests/unit/evaluation/` (mirrors the existing
`tests/unit/dofns/`, `tests/unit/engines/` layout):

- `test_profile.py`, `test_sampling.py`, `test_metrics_t1.py`,
  `test_metrics_t2.py`, `test_gate.py` — small, hand-built fixture
  DataFrames/row-lists (≤ 20 rows is enough to exercise every formula), no
  Beam required. `test_sampling.py` specifically asserts (a) determinism —
  same `run_id` + same rows fed twice ⇒ identical sample, and (b) cap
  enforcement — output never exceeds `per_stratum_cap`/50k regardless of
  input size — by calling `create_accumulator`/`add_row`/`merge_accumulators`/
  `extract_sample` directly as plain functions, the same unit-testing
  approach `MergeProfilesFn`'s commutativity requirement already implies for
  the whylogs profile merge.
- `test_metrics_t3.py` uses `pytest.importorskip("syntheval")` /
  `pytest.importorskip("evidently")` so it skips cleanly on the laptop
  (`eval-extra` not installed) rather than needing a new pytest marker wired
  into CLAUDE.md's documented `pytest -m "not gpu and not gcp"` baseline —
  deliberately minimizing this design's footprint on the existing verify
  recipe.
- Beam-side tests (`tests/unit/dofns/test_evaluation.py`) exercise
  `StratifiedReservoirFn`/`EvaluationDoFn` via `TestPipeline`/`DirectRunner`
  with a `FakeModelClient`-style small fixture, same pattern as the existing
  DoFn test suite.

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

## Figure provenance

Regenerate: `uv run --no-sync python3 scripts/doc/make_eval_figures.py` (prints
OKLab palette separation on every run). Concept figures: seeded,
deterministic, parameters in the script's `CONCEPT` block; no measured run
numbers (this design is not implemented — there are no runs to measure).

| Figure | File | Claim |
|---|---|---|
| 1 | `assets/eval-ks-vs-wasserstein.png` | same W₁, 5× different KS — the two statistics see different failures |
| 2 | `assets/eval-dcr-nndr.png` | DCR catches the parked copy; NNDR catches the unambiguous neighbor |
| inline | mermaid (house classes) | evaluation branch placement; deterministic stratified reservoir |

External references:
[`scipy.stats.ks_2samp`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.ks_2samp.html) ·
[`scipy.stats.wasserstein_distance`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.wasserstein_distance.html) ·
[Gower 1971](https://doi.org/10.2307/2528823) ·
[Giomi et al. 2022 (anonymeter)](https://arxiv.org/abs/2211.10459) ·
[Carlini et al. 2021](https://arxiv.org/abs/2012.07805) ·
[SDMetrics QualityReport](https://docs.sdv.dev/sdmetrics/reports/quality-report) ·
[SDMetrics KSComplement](https://docs.sdv.dev/sdmetrics/metrics/quality-metrics/kscomplement) ·
[Evidently](https://docs.evidentlyai.com/) —
retrieval date for all URLs: 2026-08-05.

## Metric catalogue (generated)

<!-- eval-catalogue:start -->
<!-- Generated by scripts/doc/render_eval_catalogue.py from packages/sdfb-evaluation/src/sdfb_evaluation/catalogue/metrics.yaml. Do not edit: change the YAML and run the script. -->

Metric catalogue `1.0.0`: 79 metrics over 7 levels and 5 families.

| level | fidelity | privacy | integrity | diversity | overall | total |
| --- | --- | --- | --- | --- | --- | --- |
| field | 3 | 3 | 1 | — | — | 7 |
| column | 19 | — | 1 | 7 | — | 27 |
| pair | 5 | — | — | — | — | 5 |
| row | 2 | 9 | — | 2 | — | 13 |
| table | 7 | 1 | 4 | 1 | 1 | 14 |
| relationship | 5 | — | 2 | 1 | — | 8 |
| model | 1 | 1 | 1 | 1 | 1 | 5 |
| **total** | 42 | 14 | 9 | 12 | 2 | 79 |

#### field (7)

| metric | family | kinds | formula | estimator | direction | target | warn | fail | score | noise floor | flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `field.category_adherence`<br/>Category adherence | fidelity | categorical, boolean | $`\operatorname{CA} = \frac{1}{\lvert Y \rvert} \sum_{y \in Y} \mathbf{1}\left[ y \in \mathcal{D}_{src} \right]`$ | exact/value_sampled | higher_better | 1 | 0.99 | 0.95 | linear | wilson | — |
| `field.range_adherence`<br/>Range adherence | fidelity | numeric, temporal | $`\operatorname{RA} = \frac{1}{\lvert Y \rvert} \sum_{y \in Y} \mathbf{1}\left[ \min_{src} \le y \le \max_{src} \right]`$ | binned | higher_better | 1 | 0.99 | 0.95 | linear | wilson | — |
| `field.shape_adherence`<br/>Shape (mask) adherence | fidelity | text, identifier | $`\operatorname{SA} = \frac{1}{\lvert Y \rvert} \sum_{y \in Y} \mathbf{1}\left[ \operatorname{mask}(y) \in H_{src} \cup T_{src} \right]`$ | exact | higher_better | 1 | 0.95 | 0.8 | linear | wilson | baseline |
| `field.substantive_copy_rate`<br/>Substantive copy rate | privacy | categorical, text, identifier, numeric, temporal | $`\operatorname{SCR} = \frac{1}{\lvert Y_{sub} \rvert} \sum_{y \in Y_{sub}} \mathbf{1}\left[ 1 \le c_{src}(y) < 10 \right]`$ | exact/value_sampled | lower_better | — | 0.0001 | 0.001 | linear | wilson | — |
| `field.value_memorization_lift`<br/>Value memorization lift (reference vs holdout) | privacy | categorical, text, identifier, numeric, temporal | $`\operatorname{lift} = \frac{m_R / \lvert V_R \rvert}{m_H / \lvert V_H \rvert}, \quad \text{CI at } \alpha / m`$ | exact/value_sampled | lower_better | 1 | 2 | 5 | linear | rate_ratio | CI bound |
| `field.pool_memorization_lift`<br/>Pool memorization lift (LLM free-text pools) | privacy | text | $`\operatorname{lift}_{pool} = \frac{m_R / \lvert V_R \rvert}{m_H / \lvert V_H \rvert}, \quad m_S = \lvert P \cap V_S \rvert`$ | exact/value_sampled | lower_better | 1 | 2 | 5 | linear | rate_ratio | CI bound |
| `field.type_validity`<br/>Type validity | integrity | numeric, temporal, categorical, boolean, text, identifier, nested | $`\operatorname{TV} = \frac{1}{\lvert Y \rvert} \sum_{y \in Y} \mathbf{1}\left[ y \text{ parses as the declared type} \right]`$ | exact | higher_better | — | 0.9999 | 0.999 | linear | — | — |

#### column (27)

| metric | family | kinds | formula | estimator | direction | target | warn | fail | score | noise floor | flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `column.null_rate_delta`<br/>Null-rate difference | fidelity | numeric, temporal, categorical, boolean, text, identifier, nested | $`\Delta_{null} = \lvert p^{syn}_{null} - p^{src}_{null} \rvert`$ | exact | lower_better | — | 0.02 | 0.05 | linear | newcombe | baseline |
| `column.empty_rate_delta`<br/>Empty-string-rate difference | fidelity | categorical, text, identifier | $`\Delta_{empty} = \lvert p^{syn}_{empty} - p^{src}_{empty} \rvert`$ | exact | lower_better | — | 0.02 | 0.05 | linear | newcombe | baseline |
| `column.ks`<br/>Kolmogorov–Smirnov distance (exact at bin edges) | fidelity | numeric, temporal | $`D_{lo} = \max_i \lvert F_{src}(e_i) - F_{syn}(e_i) \rvert`$ | binned | lower_better | — | 0.1 | 0.2 | complement | ks_two_sample | baseline |
| `column.pit_w1`<br/>PIT Wasserstein-1 (scale-free) | fidelity | numeric, temporal | $`W_1^{PIT} = \int \lvert F_{syn}(x) - F_{src}(x) \rvert \, dF_{src}(x) \in \left[ 0, \frac{1}{2} \right]`$ | binned | lower_better | — | 0.05 | 0.1 | linear | — | baseline |
| `column.wasserstein`<br/>Wasserstein-1 distance (column units) | fidelity | numeric, temporal | $`W_1 = \int_{-\infty}^{\infty} \lvert F_{syn}(x) - F_{src}(x) \rvert \, dx`$ | binned | lower_better | — | — | — | none | — | baseline |
| `column.decile_ks_legacy`<br/>Decile KS (legacy, for continuity) | fidelity | numeric | $`D_{dec} = \max_{x \in G} \lvert \tilde{F}_{src}(x) - \tilde{F}_{syn}(x) \rvert, \quad G = Q^{src}_{0..10} \cup Q^{syn}_{0..10}`$ | binned | lower_better | — | 0.2 | 0.4 | complement | — | baseline |
| `column.jsd`<br/>Jensen–Shannon divergence (bits) | fidelity | numeric, temporal, categorical, boolean | $`\operatorname{JSD}(p, q) = \frac{1}{2} \sum_k p_k \log_2 \frac{p_k}{m_k} + \frac{1}{2} \sum_k q_k \log_2 \frac{q_k}{m_k}, \quad m = \frac{p + q}{2}`$ | exact/binned/value_sampled | lower_better | — | 0.05 | 0.1 | linear | jsd_null | baseline |
| `column.tvd`<br/>Total variation distance (categories) | fidelity | categorical, boolean | $`\operatorname{TVD}(p, q) = 1 - \sum_k \min(p_k, q_k) = \frac{1}{2} \sum_k \lvert p_k - q_k \rvert`$ | exact/value_sampled | lower_better | — | 0.1 | 0.2 | complement | tvd_null | baseline |
| `column.psi`<br/>Population stability index (deciles) | fidelity | numeric, temporal | $`\operatorname{PSI} = \sum_{b=1}^{B} (q_b - p_b) \ln \frac{q_b}{p_b}, \quad p_b = \frac{c^{src}_b + 0.5}{n_{src} + 0.5 B}`$ | binned | lower_better | — | 0.1 | 0.25 | linear | — | baseline |
| `column.smd`<br/>Standardized mean difference | fidelity | numeric, temporal | $`\operatorname{SMD} = \frac{\lvert \mu_{syn} - \mu_{src} \rvert}{\sigma_{src}}`$ | exact | lower_better | — | 0.1 | 0.2 | linear | — | baseline |
| `column.std_ratio`<br/>Standard-deviation ratio | fidelity | numeric, temporal | $`\operatorname{SR} = \frac{\sigma_{syn}}{\sigma_{src}}`$ | exact | target | 1 | 0.1 | 0.25 | ratio_to_one | — | baseline |
| `column.zero_rate_delta`<br/>Zero-rate difference | fidelity | numeric | $`\Delta_{0} = \lvert p^{syn}_{0} - p^{src}_{0} \rvert`$ | exact | lower_better | — | 0.02 | 0.05 | linear | newcombe | baseline |
| `column.range_coverage`<br/>Range coverage | diversity | numeric, temporal | $`\operatorname{RC} = \frac{\max\left(0, \min(M_{syn}, M_{src}) - \max(m_{syn}, m_{src})\right)}{M_{src} - m_{src}}`$ | binned | higher_better | — | 0.9 | 0.75 | linear | — | baseline |
| `column.dow_tvd`<br/>Day-of-week mix (TVD) | fidelity | temporal | $`\operatorname{TVD}_{dow} = \frac{1}{2} \sum_{d=1}^{7} \lvert p_d - q_d \rvert`$ | exact | lower_better | — | 0.1 | 0.2 | complement | tvd_null | baseline |
| `column.month_tvd`<br/>Month-of-year mix (TVD) | fidelity | temporal | $`\operatorname{TVD}_{month} = \frac{1}{2} \sum_{k=1}^{12} \lvert p_k - q_k \rvert`$ | exact | lower_better | — | 0.1 | 0.2 | complement | tvd_null | baseline |
| `column.hour_tvd`<br/>Hour-of-day mix (TVD) | fidelity | temporal | $`\operatorname{TVD}_{hour} = \frac{1}{2} \sum_{h=0}^{23} \lvert p_h - q_h \rvert`$ | exact | lower_better | — | 0.1 | 0.2 | complement | tvd_null | baseline |
| `column.cohens_w`<br/>Cohen's w (category effect size) | fidelity | categorical, boolean | $`w = \sqrt{\sum_{k : p_k > 0} \frac{(q_k - p_k)^2}{p_k}}`$ | exact/value_sampled | lower_better | — | 0.1 | 0.3 | linear | — | baseline |
| `column.top1_share_delta`<br/>Top-value share difference | diversity | categorical, boolean | $`\Delta_{top1} = \lvert \max_k q_k - \max_k p_k \rvert`$ | exact/value_sampled | lower_better | — | 0.05 | 0.15 | linear | newcombe | baseline |
| `column.coverage_mass`<br/>Coverage mass | diversity | categorical, boolean | $`\operatorname{Cov} = \sum_{v \in S \cap Y} p_v`$ | exact/value_sampled | higher_better | — | 0.95 | 0.8 | linear | — | baseline |
| `column.novelty_mass`<br/>Novelty mass vs the source's unseen mass | diversity | categorical, text, identifier | $`\nu_{syn} = \sum_{v \notin S} q_v, \quad t = \hat{\nu}_{src} = \frac{f_1}{N}, \quad d = \lvert \nu_{syn} - t \rvert`$ | exact/value_sampled | target | source value | 0.1 | 0.25 | ratio_to_one | — | — |
| `column.entropy_ratio`<br/>Entropy ratio at matched n | diversity | categorical, boolean, text, identifier | $`\operatorname{ER} = \frac{\hat{H}^{(m)}_{syn}}{\hat{H}^{(m)}_{src}}, \quad \hat{H}^{(m)} = -\sum_v \frac{c_v}{m} \log_2 \frac{c_v}{m}`$ | exact/value_sampled | target | 1 | 0.1 | 0.25 | ratio_to_one | — | matched n, baseline |
| `column.distinct_ratio`<br/>Distinct-count ratio at matched n | diversity | categorical, text, identifier | $`\operatorname{DR} = \frac{K^{(m)}_{syn}}{K^{(m)}_{src}}`$ | exact/value_sampled | target | 1 | 0.1 | 0.3 | ratio_to_one | — | matched n, baseline |
| `column.distinct_ceiling_hit`<br/>Distinct-count ceiling hit (pool cap) | diversity | text | $`\operatorname{hit} = \mathbf{1}\left[ K_{syn} \in \{ 512, K_{target} \} \right]`$ | exact | lower_better | — | 0.5 | 1 | none | — | — |
| `column.length_ks`<br/>String-length KS | fidelity | categorical, text, identifier | $`D_{len} = \max_{\ell} \lvert F^{len}_{src}(\ell) - F^{len}_{syn}(\ell) \rvert`$ | binned | lower_better | — | 0.1 | 0.2 | complement | ks_two_sample | baseline |
| `column.shape_head_tv`<br/>Shape head total variation | fidelity | text, identifier | $`\operatorname{TV}_{head} = \frac{1}{2} \left( \sum_{s \in H_{src}} \lvert p_s - q_s \rvert + \lvert p_{tail} - q_{tail} \rvert \right)`$ | exact | lower_better | — | 0.1 | 0.2 | complement | tvd_null | baseline |
| `column.char_class_l1`<br/>Character-class profile distance | fidelity | categorical, text, identifier | $`L_1 = \sum_{k \in K} \lvert f^{syn}_k - f^{src}_k \rvert, \quad K = \{ \text{digit}, \text{upper}, \text{lower}, \text{space}, \text{punct} \}`$ | exact | lower_better | — | 0.1 | 0.2 | linear | — | baseline |
| `column.source_stats_drift`<br/>Source-stats drift (evaluator vs generator) | integrity | numeric, temporal, categorical, boolean, text, identifier | $`\delta = \max\left( \lvert \Delta p_{null} \rvert,\, \max_{i=1}^{9} \operatorname{dist}\left( \frac{i}{10}, \left[ F^{-}_{src}(d_i), F_{src}(d_i) \right] \right),\, \frac{\lvert D_{gen} - D_{src} \rvert}{\max(D_{gen}, D_{src})} \right)`$ | exact | lower_better | — | 0.05 | 0.15 | none | — | — |

#### pair (5)

| metric | family | kinds | formula | estimator | direction | target | warn | fail | score | noise floor | flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `pair.pearson_delta`<br/>Pearson correlation difference | fidelity | numeric, temporal | $`\Delta\rho = \lvert \rho_{src} - \rho_{syn} \rvert`$ | exact | lower_better | — | 0.1 | 0.2 | linear | fisher_z | baseline |
| `pair.spearman_delta`<br/>Spearman rank-correlation difference | fidelity | numeric, temporal | $`\Delta\rho^S = \lvert \rho^S_{src} - \rho^S_{syn} \rvert, \quad \rho^S = \operatorname{corr}\left( F_X(x), F_Y(y) \right)`$ | binned | lower_better | — | 0.1 | 0.2 | linear | fisher_z | baseline |
| `pair.cramers_v_delta`<br/>Cramér's V difference (bias-corrected) | fidelity | categorical, boolean, numeric, temporal | $`\Delta\tilde{V} = \lvert \tilde{V}_{src} - \tilde{V}_{syn} \rvert, \quad \tilde{V} = \sqrt{\frac{\tilde{\varphi}^2}{\min(\tilde{k} - 1, \tilde{r} - 1)}}, \quad \tilde{\varphi}^2 = \max\left(0, \frac{\chi^2}{n} - \frac{(k-1)(r-1)}{n-1}\right), \quad \tilde{k} = k - \frac{(k-1)^2}{n-1}, \quad \tilde{r} = r - \frac{(r-1)^2}{n-1}`$ | exact | lower_better | — | 0.1 | 0.2 | linear | — | baseline |
| `pair.nmi_delta`<br/>Normalized mutual information difference | fidelity | categorical, boolean, numeric, temporal | $`\Delta\operatorname{NMI} = \lvert \operatorname{NMI}_{src} - \operatorname{NMI}_{syn} \rvert, \quad \operatorname{NMI} = \frac{I(X; Y)}{\min(H_X, H_Y)}`$ | exact | lower_better | — | 0.05 | 0.15 | linear | mi_bias | baseline |
| `pair.contingency_tvd`<br/>Contingency-table total variation | fidelity | categorical, boolean, numeric, temporal | $`\operatorname{TVD}_{2D} = \frac{1}{2} \sum_{a, b} \lvert p_{ab} - q_{ab} \rvert`$ | exact | lower_better | — | 0.1 | 0.2 | complement | tvd_null | baseline |

#### row (13)

| metric | family | kinds | formula | estimator | direction | target | warn | fail | score | noise floor | flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `row.exact_match_rate`<br/>Exact row match rate | privacy | — | $`\operatorname{EMR} = \frac{1}{n_{syn}} \sum_{i} \mathbf{1}\left[ h(y_i) \in h(\text{source}) \right]`$ | exact | lower_better | — | 1e-5 | 0.0001 | linear | wilson | — |
| `row.exact_match_rate_nonkey`<br/>Exact row match rate (keys excluded) | privacy | — | $`\operatorname{EMR}_{nk} = \frac{1}{n_{syn}} \sum_{i} \mathbf{1}\left[ h_{nk}(y_i) \in h_{nk}(\text{source}) \right]`$ | exact | lower_better | — | 0.001 | 0.01 | linear | wilson | — |
| `row.memorization_lift`<br/>Row memorization lift (reference vs holdout) | privacy | — | $`\operatorname{lift} = \frac{m_R / \lvert R \cap \bar{H} \rvert}{m_H / \lvert H \cap \bar{R} \rvert}, \quad m_S = \text{distinct exclusive records of } S \text{ the synthetic side reproduces}`$ | exact | lower_better | 1 | 2 | 5 | linear | rate_ratio | CI bound |
| `row.exposure_lift`<br/>Prompt-exposure lift | privacy | — | $`\operatorname{lift}_E = \frac{m_E / \lvert E \cap \bar{H} \rvert}{m_{H_E} / \lvert H_E \cap \bar{R} \rvert}, \quad m_S = \text{distinct exclusive records of } S \text{ reproduced}`$ | exact | lower_better | 1 | 2 | 5 | linear | rate_ratio | CI bound |
| `row.near_match_rate`<br/>Near-match rate (all but one column) | privacy | — | $`\operatorname{NMR} = \frac{1}{n_{syn}} \sum_i \mathbf{1}\left[ \exists x \in R, \exists j : h_{-j}(y_i) = h_{-j}(x) \text{ and } h_{nk}(y_i) \notin h_{nk}(R \cup H) \right]`$ | exact | lower_better | — | 0.001 | 0.01 | linear | wilson | — |
| `row.near_match_lift`<br/>Near-match lift (reference vs holdout) | privacy | — | $`\operatorname{lift}_{near} = \frac{m^{near}_R / \lvert L_R \cap \bar{L}_H \rvert}{m^{near}_H / \lvert L_H \cap \bar{L}_R \rvert}, \quad L_S = \text{leave-one-out keys of } S \text{, per column}`$ | exact | lower_better | 1 | 2 | 5 | linear | rate_ratio | CI bound |
| `row.internal_duplicate_excess`<br/>Internal duplicate excess | diversity | — | $`\Delta_{dup} = \frac{\operatorname{E}[D_m]_{syn} - \operatorname{E}[D_m]_{src}}{m}, \quad \operatorname{E}[D_m] = \sum_{c} f_c \left( \frac{c\,m}{N} - \Pr(X_c = 1) \right), \quad m = \min(n_{src}, n_{syn})`$ | exact | lower_better | — | 0.01 | 0.05 | linear | newcombe | matched n, baseline |
| `row.dcr_train_holdout_share`<br/>Closer-to-reference share (DCR holdout test) | privacy | — | $`\operatorname{share} = \frac{1}{n} \sum_i \left( \mathbf{1}\left[ d_R(y_i) < d_H(y_i) \right] + \frac{1}{2} \mathbf{1}\left[ d_R(y_i) = d_H(y_i) \right] \right)`$ | sample | lower_better | 0.5 | 0.55 | 0.6 | linear | wilson | matched n, CI bound |
| `row.dcr_p5_ratio`<br/>Distance to closest record, 5th-percentile ratio | privacy | — | $`\operatorname{DCR}_{p5} = \frac{Q_{0.05}\left( d(Y \to R) \right)}{Q_{0.05}\left( d(H \to R) \right)}`$ | sample | higher_better | — | 0.8 | 0.5 | linear | — | matched n |
| `row.nndr_p5_ratio`<br/>Nearest-neighbour distance ratio, 5th-percentile ratio | privacy | — | $`\operatorname{NNDR}_{p5} = \frac{Q_{0.05}\left( d_1 / d_2 \text{ of } Y \right)}{Q_{0.05}\left( d_1 / d_2 \text{ of } H \right)}`$ | sample | higher_better | — | 0.8 | 0.5 | linear | — | matched n |
| `row.density`<br/>Density (k-NN precision) | fidelity | — | $`\operatorname{density} = \frac{1}{k M} \sum_{j=1}^{M} \sum_{i=1}^{N} \mathbf{1}\left[ Y_j \in B\left( X_i, \operatorname{NND}_k(X_i) \right) \right]`$ | sample | target | 1 | 0.2 | 0.4 | ratio_to_one | — | matched n, baseline |
| `row.coverage`<br/>Coverage (k-NN recall) | diversity | — | $`\operatorname{coverage} = \frac{1}{N} \sum_{i=1}^{N} \mathbf{1}\left[ \exists j : Y_j \in B\left( X_i, \operatorname{NND}_k(X_i) \right) \right]`$ | sample | higher_better | — | 0.85 | 0.7 | linear | — | matched n, baseline |
| `row.null_pattern_tvd`<br/>Null-pattern total variation | fidelity | — | $`\operatorname{TVD}_{null} = \frac{1}{2} \sum_{b} \lvert p_b - q_b \rvert, \quad b \in \text{top-64 source patterns} \cup \{ \text{tail} \}`$ | exact | lower_better | — | 0.1 | 0.2 | complement | tvd_null | baseline |

#### table (14)

| metric | family | kinds | formula | estimator | direction | target | warn | fail | score | noise floor | flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `table.row_count_ratio`<br/>Row-count ratio | integrity | — | $`\operatorname{RCR} = \frac{n_{syn}}{n_{expected}}`$ | exact | target | 1 | 0.01 | 0.05 | ratio_to_one | — | — |
| `table.pk_duplicate_rate`<br/>Primary-key duplicate rate | integrity | — | $`\operatorname{dup}_{pk} = \frac{1}{n_{syn}} \sum_i \mathbf{1}\left[ c\left( \operatorname{pk}(y_i) \right) \ge 2 \right]`$ | exact | lower_better | — | 0 | 0 | linear | — | — |
| `table.identity_duplicate_rate`<br/>Identity-column duplicate rate | integrity | — | $`\operatorname{dup}_{id} = \frac{1}{n_{syn}} \sum_i \mathbf{1}\left[ c\left( \operatorname{id}(y_i) \right) \ge 2 \right]`$ | exact | lower_better | — | 0 | 0 | linear | — | — |
| `table.detection_auc`<br/>Detection AUC (classifier two-sample test) | fidelity | — | $`\operatorname{AUC} = \Pr\left( \hat{s}(y) > \hat{s}(x) \right) + \frac{1}{2} \Pr\left( \hat{s}(y) = \hat{s}(x) \right)`$ | sample | lower_better | 0.5 | 0.7 | 0.85 | auc | delong | matched n, baseline |
| `table.pmse_ratio`<br/>Propensity MSE ratio | fidelity | — | $`\frac{\operatorname{pMSE}}{E_0}, \quad \operatorname{pMSE} = \frac{1}{N} \sum_{i=1}^{N} (\hat{\pi}_i - c)^2, \quad E_0 = \frac{(k-1)\, c\, (1-c)}{N}`$ | sample | lower_better | — | 3 | 10 | linear | — | matched n, baseline |
| `table.corr_rms_delta`<br/>Correlation-matrix RMS difference | fidelity | — | $`\operatorname{RMS}_{\Delta\rho} = \sqrt{\frac{2}{d(d-1)} \sum_{a < b} \left( \rho^{src}_{ab} - \rho^{syn}_{ab} \right)^2}`$ | exact | lower_better | — | 0.05 | 0.1 | linear | — | baseline |
| `table.corr_max_delta`<br/>Correlation-matrix maximum difference | fidelity | — | $`\max_{a < b} \lvert \rho^{src}_{ab} - \rho^{syn}_{ab} \rvert`$ | exact | lower_better | — | 0.15 | 0.3 | linear | — | baseline |
| `table.column_shape_score`<br/>Column shape score | fidelity | — | $`\operatorname{CSS} = \frac{1}{\lvert C \rvert} \sum_{c \in C} \bar{s}_c`$ | exact | higher_better | — | 0.85 | 0.7 | none | — | — |
| `table.pair_trend_score`<br/>Column pair trend score | fidelity | — | $`\operatorname{PTS} = \frac{1}{\lvert P \rvert} \sum_{(a, b) \in P} \bar{s}_{ab}`$ | exact | higher_better | — | 0.85 | 0.7 | none | — | — |
| `table.fidelity_score`<br/>Table fidelity score | fidelity | — | $`S_{fidelity} = \frac{1}{\lvert U_{f} \rvert} \sum_{u \in U_{f}} \bar{s}_{u}, \quad U_{f} = C \cup \{ P \} \cup M_{row} \cup M_{table} \cup E_{child}`$ | exact | higher_better | — | 0.85 | 0.7 | none | — | — |
| `table.privacy_score`<br/>Table privacy score | privacy | — | $`S_{privacy} = \frac{1}{\lvert U_{f} \rvert} \sum_{u \in U_{f}} \bar{s}_{u}, \quad U_{f} = C \cup \{ P \} \cup M_{row} \cup M_{table} \cup E_{child}`$ | exact | higher_better | — | 0.85 | 0.7 | none | — | — |
| `table.integrity_score`<br/>Table integrity score | integrity | — | $`S_{integrity} = \frac{1}{\lvert U_{f} \rvert} \sum_{u \in U_{f}} \bar{s}_{u}, \quad U_{f} = C \cup \{ P \} \cup M_{row} \cup M_{table} \cup E_{child}`$ | exact | higher_better | — | 0.85 | 0.7 | none | — | — |
| `table.diversity_score`<br/>Table diversity score | diversity | — | $`S_{diversity} = \frac{1}{\lvert U_{f} \rvert} \sum_{u \in U_{f}} \bar{s}_{u}, \quad U_{f} = C \cup \{ P \} \cup M_{row} \cup M_{table} \cup E_{child}`$ | exact | higher_better | — | 0.85 | 0.7 | none | — | — |
| `table.overall_score`<br/>Table overall score | overall | — | $`S_{overall} = \frac{1}{\lvert F \rvert} \sum_{f \in F} S_f`$ | exact | higher_better | — | 0.85 | 0.7 | none | — | — |

#### relationship (8)

| metric | family | kinds | formula | estimator | direction | target | warn | fail | score | noise floor | flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `relationship.orphan_rate`<br/>Orphan rate (foreign key) | integrity | — | $`\operatorname{orphan} = \frac{\#\{ \text{non-null child key tuples with no parent} \}}{\#\{ \text{non-null child key tuples} \}}`$ | exact | lower_better | — | 0 | 0 | linear | — | — |
| `relationship.orphan_rate_source`<br/>Orphan rate on the source (baseline) | integrity | — | $`\operatorname{orphan}_{src} = \frac{\#\{ \text{non-null source child key tuples with no parent} \}}{\#\{ \text{non-null source child key tuples} \}}`$ | exact | lower_better | — | — | — | none | — | — |
| `relationship.fanout_tvd`<br/>Fan-out distribution (TVD) | fidelity | — | $`\operatorname{TVD}_{fan} = \frac{1}{2} \sum_{c \in \{0, \dots, 49, \ge 50\}} \lvert p_c - q_c \rvert`$ | exact | lower_better | — | 0.1 | 0.2 | complement | tvd_null | — |
| `relationship.fanout_w1`<br/>Fan-out Wasserstein-1 (children per parent) | fidelity | — | $`W_1^{fan} = \sum_i (x_{i+1} - x_i) \lvert F^{fan}_{src}(x_i) - F^{fan}_{syn}(x_i) \rvert, \quad \{x_i\} = \{0, \dots, 49\} \cup \{\bar c_{src}, \bar c_{syn}\}`$ | exact | lower_better | — | — | — | none | — | — |
| `relationship.fanout_mean_ratio`<br/>Mean fan-out ratio | fidelity | — | $`\operatorname{FMR} = \frac{n^{syn}_{child} / n^{syn}_{parent}}{n^{src}_{child} / n^{src}_{parent}}`$ | exact | target | 1 | 0.05 | 0.15 | ratio_to_one | — | — |
| `relationship.zero_child_share_delta`<br/>Childless-parent share difference | fidelity | — | $`\Delta_z = \lvert z_{syn} - z_{src} \rvert, \quad z = \frac{\#\{ \text{parents with no child} \}}{n_{parent}}`$ | exact | lower_better | — | 0.02 | 0.05 | linear | newcombe | — |
| `relationship.cardinality_adherence`<br/>Cardinality adherence | fidelity | — | $`\operatorname{CBA} = \frac{1}{n^{syn}_{parent}} \sum_{p} \mathbf{1}\left[ \min_{src} \le c_p \le \max_{src} \right]`$ | exact | higher_better | 1 | 0.99 | 0.95 | linear | wilson | — |
| `relationship.parent_coverage`<br/>Parent coverage | diversity | — | $`\operatorname{PC} = \frac{\Pr_{syn}\left[ c_p \ge 1 \right]}{\Pr_{src}\left[ c_p \ge 1 \right]}`$ | exact | higher_better | — | 0.95 | 0.8 | linear | — | — |

#### model (5)

| metric | family | kinds | formula | estimator | direction | target | warn | fail | score | noise floor | flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `model.overall_score`<br/>Model overall score | overall | — | $`S^{model}_{overall} = \frac{1}{\lvert F \rvert} \sum_{f \in F} S^{model}_{f}`$ | exact | higher_better | — | 0.85 | 0.7 | none | — | — |
| `model.fidelity_score`<br/>Model fidelity score | fidelity | — | $`S^{model}_{fidelity} = \frac{1}{\lvert T \rvert} \sum_{t \in T} S_{t, fidelity}`$ | exact | higher_better | — | 0.85 | 0.7 | none | — | — |
| `model.privacy_score`<br/>Model privacy score | privacy | — | $`S^{model}_{privacy} = \frac{1}{\lvert T \rvert} \sum_{t \in T} S_{t, privacy}`$ | exact | higher_better | — | 0.85 | 0.7 | none | — | — |
| `model.integrity_score`<br/>Model integrity score | integrity | — | $`S^{model}_{integrity} = \frac{1}{\lvert T \rvert} \sum_{t \in T} S_{t, integrity}`$ | exact | higher_better | — | 0.85 | 0.7 | none | — | — |
| `model.diversity_score`<br/>Model diversity score | diversity | — | $`S^{model}_{diversity} = \frac{1}{\lvert T \rvert} \sum_{t \in T} S_{t, diversity}`$ | exact | higher_better | — | 0.85 | 0.7 | none | — | — |
<!-- eval-catalogue:end -->
