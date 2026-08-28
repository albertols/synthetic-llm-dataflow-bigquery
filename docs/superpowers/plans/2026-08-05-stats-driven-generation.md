# Stats-Driven Generation (source_table_stats as a generation input) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote `source_table_stats` from a human/drift view into a bounded generation input: richer Tier-1 stats (entropy, skew, deciles, temporal shape, null-pattern mix), a Tier-2 exact aggregate pass (`--source_stats=exact`, one table scan), and three contained engine consumers (B.2 inverse-CDF numeric/temporal sampling, B.1 pool sizing from exact distinct, measured length hints in pool prompts) — with docs citing primary sources.

**Architecture:** Workers stay stats-table-agnostic (2026-08-05 spec WS-B invariant): every generation consumer reads either the worker-side `ColumnProfile` (enriched at profile time from the same reference rows) or a value threaded through `GenerationContext` by the driver. The BQ table gains provenance columns (`sample_rows`, `stats_tier`, `profiler_version`) so its append-only skip key survives profiler evolution and the Tier-1→Tier-2 meaning change of `distinct`.

**Tech Stack:** stdlib-only in `sdfb-core` (statistics via sorted lists + Counter; numpy stays deferred-imported in samplers); `google-cloud-bigquery` driver-side only in `sdfb-beam` (single-SELECT aggregate: `APPROX_COUNT_DISTINCT`, `APPROX_QUANTILES`, `APPROX_TOP_COUNT`).

## Global Constraints

- No Vertex AI, no Dataplex, no external LLM APIs (CLAUDE.md hard constraints — SQL-native profiling only).
- `sdfb-core` must not import Beam/GCP/numpy at module level (numpy only deferred inside samplers).
- Laptop-only: all tests run under `-m "not gpu and not gcp"`; BQ clients injectable mocks.
- Privacy: literal source values may land in stats ONLY for enum-routed columns (distinct ≤ 50); freetext/identifier columns get shapes/entropy/lengths, never literals (T4 exemplar-leak lesson).
- vLLM efficiency: prompt additions append AFTER the shared prefix (automatic prefix caching, ADR 0018); pool-size changes stay under `_FREE_TEXT_POOL_MAX`.
- Suite must stay green: `uv run pytest -m "not gpu and not gcp" -q`, `uv run ruff check .`, `uv run mypy packages/sdfb-core/src` (0 errors).

---

### Task 1: Stats table contract — provenance columns + versioned skip key

**Files:**
- Modify: `config/bq_schema/synthetic_rag/source_table_stats.schema.json`
- Modify: `packages/sdfb-core/src/sdfb_core/stats/source_stats.py`
- Modify: `packages/sdfb-beam/src/sdfb_beam/io/stats_store.py`
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (`_emit_source_stats`)
- Test: `packages/sdfb-tests/tests/unit/stats/test_source_stats.py`, `packages/sdfb-tests/tests/unit/io/test_stats_store.py`

**Interfaces:**
- Produces: `PROFILER_VERSION: str` module constant in `source_stats.py`; `stats_rows(..., stats_tier: str = "sample")` emitting `sample_rows`, `stats_tier`, `profiler_version` columns; `BigQuerySourceStatsStore.exists(table_fqn, reference_digest, *, profiler_version: str | None = None, stats_tier: str | None = None)`.

- [ ] Add three NULLABLE columns to the schema JSON: `sample_rows` INT64 ("Rows in the reference sample the fractions were computed over"), `stats_tier` STRING ("sample = 10k reference sample; exact = full-table aggregate pass"), `profiler_version` STRING ("profile_source_table version; part of the skip key so profiler upgrades re-write stats").
- [ ] `source_stats.py`: add `PROFILER_VERSION = "2"`; `profile_source_table` entries gain `"stats_tier": "sample"` and `"profiler_version": PROFILER_VERSION`; `stats_rows` gains `stats_tier` kwarg and emits the three new columns (`sample_rows` from `entry["sample_rows"]`).
- [ ] `stats_store.exists()`: optional keyword filters appended as `AND profiler_version = @pv` / `AND stats_tier = @tier` when provided.
- [ ] `run_pipeline._emit_source_stats`: pass `profiler_version=PROFILER_VERSION` (and tier) to `exists()`.
- [ ] Tests: schema file lists the 3 new fields; `stats_rows` row carries them; `exists()` SQL includes the filters when passed (mock client capture — follow existing test_stats_store pattern).
- [ ] Run suite subset, commit `feat(stats): provenance columns + versioned skip key for source_table_stats`.

### Task 2: Tier-1 profiler enrichment (single-pass, performance-minded)

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/stats/source_stats.py`
- Test: `packages/sdfb-tests/tests/unit/stats/test_source_stats.py`

**Interfaces:**
- Produces (JSON entry additions): `entropy` (Shannon, bits), `entropy_norm` (÷ log2(distinct), 0–1), `top1_share`, `top_values` (enum-routed only: `[[value, fraction], …]` top 8), `mean`, `stddev`, `deciles` (11 points, numeric), `temporal_min`/`temporal_max` (ISO), `dow_mix` (7 fractions), `hour_mix` (24 fractions, zero-suppressed to `[]` for day-granularity), `month_mix` (12 fractions), `future_fraction`; table-level pseudo-row `__table__` with `null_pattern_mix` (top-8 `[bitstring, fraction]`, columns ≤ 64 guard).

- [ ] Replace `set(strings)` with one `Counter(strings)` reused for: distinct, entropy (`-Σ p·log2 p`), `top1_share`, `top_values` (only when `distinct ≤ 50`).
- [ ] Numeric branch: single `sorted(numbers)` reused for min/max/deciles; mean/stddev via one pass (stdlib, no numpy).
- [ ] Temporal: parse each string ONCE into a `datetime` list (fixes the existing double `_day_granularity` parse at lines 131-132); derive day-granularity, ISO min/max, dow/hour/month histograms, `future_fraction` from that list. Only attempt for `_TEMPORAL_BQ_TYPES` or when a 64-value sniff parses.
- [ ] Table-level pass: one loop over rows building `Counter` of per-row null bitstrings (`"".join("1" if r.get(c) is None else "0" …)`); skip with a logged note above 64 columns; emit as `__table__` pseudo-column entry (`stats_rows` already iterates entries generically — confirm the pseudo-row lands with `generation_plan=""`).
- [ ] Tests: entropy of a uniform 4-category column == 2.0 bits, `entropy_norm == 1.0`; `top1_share` of a 90/10 column ≈ 0.9; deciles of 0..100 uniform; `top_values` absent when distinct > 50 (privacy); temporal ISO min/max + dow_mix sums to 1; `__table__` null-pattern row present and fractions sum to 1; `sample_rows` still correct.
- [ ] Run suite subset, commit `feat(stats): entropy/skew/deciles/temporal-shape/null-pattern Tier-1 stats`.

### Task 3: Shared measured-length hint → pool prompts (both engines, prefix-cache-safe)

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/text_shapes.py` (add `length_hint`)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (constraint assembly in `_infer_free_text_pool` + `_rotating_prompt`)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/freetext.py` (prompt at ~354-367)
- Test: `packages/sdfb-tests/tests/unit/engines/test_text_shapes.py` (or nearest existing), b1/b2 prompt tests

**Interfaces:**
- Produces: `length_hint(strings: Sequence[str]) -> str` — `""` for < 8 samples or p05==p95; else `"Most values are {p05}-{p95} characters (median {p50})."`

- [ ] Implement `length_hint` (sorted lengths, same index arithmetic as `_LEN_PCTS`).
- [ ] b1: `constraint = " ".join(s for s in (ddl_constraint, length_hint(prof.observed_values)) if s)` in both call sites, still gated by `prompt_constraints`; appended (not prepended) so the shared prompt prefix survives vLLM automatic prefix caching.
- [ ] b2: same join with `profile.text_pool`, appended after the existing constraint sentence.
- [ ] Tests: hint text for a known length distribution; empty for tiny/uniform samples; b1/b2 prompt contains the hint when flag on, absent when off.
- [ ] Run, commit `feat(engines): measured length hints in free-text pool prompts`.

### Task 4: B.2 inverse-CDF sampling (numeric + temporal)

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/fidelity.py` (ColumnProfile `+ quantiles: tuple[float, ...] = ()`; fill in numeric + temporal profile paths)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/backends.py` (`_sample_one` NUMERIC), `packages/sdfb-core/src/sdfb_core/engines/b2_library/temporal.py` (`sample_temporal` optional `quantiles`)
- Test: `packages/sdfb-tests/tests/unit/b2/…` nearest sampler tests

**Interfaces:**
- Produces: `ColumnProfile.quantiles` — 11 evenly-spaced empirical quantile points (p0..p100); `sample_temporal(minimum, maximum, value_type, fmt, n, rng, quantiles=())`.

- [ ] `profile_column` NUMERIC path: `qs = _decile_points(sorted(nums))` stored on the profile; TEMPORAL path: deciles of the epoch floats (post-sentinel-strip, post-clamp — clamp lo replaces points below floor).
- [ ] `_sample_one` NUMERIC: when `len(p.quantiles) >= 2`, `draws = np.interp(rng.random(n), np.linspace(0,1,len(q)), q)` (inverse transform sampling over the empirical CDF); uniform fallback unchanged. Same in `sample_temporal`.
- [ ] Tests: skewed source (e.g. 900×[0..10] + 100×[1000]) → sampled p50 near source p50, not near uniform midpoint (fixed rng seed); integer rounding and decimal scale still applied; temporal draws stay within [min, max] and cluster like the source.
- [ ] Run, commit `feat(b2): empirical inverse-CDF sampling for numeric and temporal columns`.

### Task 5: B.1 pool sizing from exact distinct

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/base.py` (`GenerationContext + source_distinct: dict[str, int]`)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (`_pool_target`)
- Test: b1 pool-target tests

**Interfaces:**
- Produces: `GenerationContext.source_distinct: dict[str, int] = Field(default_factory=dict)` — full-table distinct per column, driver-populated only on `--source_stats=exact`.

- [ ] `_pool_target`: `distinct = ctx.source_distinct.get(prof.name) or len(set(prof.observed_values))` — exact stats lift the sample's under-estimate (five-run verdict: sample distinct 95 vs source 4k starved the pool); `_FREE_TEXT_POOL_MAX` cap unchanged (GPU cost is linear in pool size).
- [ ] Test: hint 4000 with 95 observed → target 512; no hint → 95; hint smaller than sample (stale stats) → max(min(bounds),1) semantics keep it sane.
- [ ] Run, commit `feat(b1): source_distinct-aware free-text pool sizing`.

### Task 6: Tier-2 exact stats (`--source_stats=exact`) — one scan, driver-side

**Files:**
- Create: `packages/sdfb-beam/src/sdfb_beam/io/exact_stats.py`
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (arg choices, `_emit_source_stats` merge + return `source_distinct`, thread into pipeline ctx)
- Modify: `packages/sdfb-beam/src/sdfb_beam/pipeline.py` (accept/forward `source_distinct` if ctx built there)
- Test: `packages/sdfb-tests/tests/unit/io/test_exact_stats.py`, run_pipeline wiring test

**Interfaces:**
- Produces: `compute_exact_stats(table_fqn, table_schema, tier1_stats, *, client=None, top_k=8) -> dict[str, dict]` — merged copy of tier1 with `source_rows`, exact `distinct`/`distinct_ratio`, `deciles`(exact), `mean`/`stddev`, `top_values` (enum-routed only), `stats_tier="exact"`.

- [ ] Single `SELECT` string built from the schema columns (the `scripts/e2e/freetext_crosscheck.py::_aggregate` pattern): per column `COUNT(*)`, `COUNTIF(c IS NULL)`, `COUNTIF(TRIM(CAST(c AS STRING))='')`, `APPROX_COUNT_DISTINCT(c)`; numerics add `APPROX_QUANTILES(c, 10)`, `AVG`, `STDDEV`; enum-routed (tier1 distinct ≤ 50) add `TO_JSON_STRING(APPROX_TOP_COUNT(CAST(c AS STRING), 8))`. ONE table scan for everything; injectable client, no module-level bigquery import.
- [ ] `_emit_source_stats`: `exact` mode computes tier1, merges exact, writes rows with `stats_tier="exact"` (skip key includes tier), returns `{col: distinct}`; caller threads into `GenerationContext.source_distinct`.
- [ ] Tests with a mock client returning a canned aggregate row: merge semantics, privacy gate (no `top_values` for high-cardinality), single query issued.
- [ ] Run, commit `feat(stats): Tier-2 exact aggregate pass wired to pool sizing`.

### Task 7: Research pass + docs with citations

**Files:**
- Create: `docs/adr/0022-stats-driven-generation.md`
- Create: `docs/designs/2026-08-05-source-table-stats.md` (visual-first: mermaid flow + per-kind consumer table)
- Modify: `docs/DEPLOYMENT_PREREQUISITES.md` (step 11: new columns, tier semantics)
- Modify: `CLAUDE.md` (+1 line: cite primary sources in design docs), `.claude/skills/visual-first-documentation/SKILL.md` (citation rule)
- Modify: `docs/ROADMAP.md` if it tracks WS-B

- [ ] WebSearch for primary sources; verify links resolve: GReaT (Borisov et al., ICLR 2023), CTGAN (Xu et al., NeurIPS 2019), inverse transform sampling, HLL++ (Heule et al. 2013 — behind `APPROX_COUNT_DISTINCT`), BigQuery approximate aggregation docs, vLLM automatic prefix caching docs, SDMetrics/SDV category-coverage + KSComplement metrics.
- [ ] ADR 0022: decision (stats as bounded generation input; workers stats-table-agnostic), tiers, the three consumers, privacy gate, alternatives rejected (Dataplex — banned; joint copulas — M2), citations.
- [ ] Design doc: load `visual-first-documentation` skill first; mermaid of tiered flow + consumer map; per-generation-kind table (constant/categorical/integer/dates/freetext → which stat feeds it and which does NOT yet — honesty column).
- [ ] Commit `docs: ADR 0022 + stats-driven generation design (cited)`.

### Task 8: Full verification

- [ ] `uv run pytest -m "not gpu and not gcp" -q` — all green (853+).
- [ ] `uv run ruff check .` clean; `uv run mypy packages/sdfb-core/src` 0 errors.
- [ ] Final commit if stragglers; do NOT touch `composer/synthetic_beam_bigquery.py`'s pre-existing uncommitted edit.

## Self-Review

- Spec coverage: schema hardening (T1), enrichment+perf (T2), prompt hints/vLLM (T3), B.2 marginals (T4), pool sizing (T5), Tier 2 (T6), docs+citations+persistence (T7), gates (T8). Deferred consciously: weekday-preserving temporal buckets, joint correlations (documented in ADR as M2).
- Type consistency: `quantiles: tuple[float, ...]`, `source_distinct: dict[str, int]`, `stats_tier: str` used consistently across tasks.
