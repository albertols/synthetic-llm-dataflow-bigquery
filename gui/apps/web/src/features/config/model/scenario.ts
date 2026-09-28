/**
 * The scenario calculator ("amplifier"): what a reference sample of n rows,
 * drawn from a source of N rows, can and cannot tell a generator asked for M
 * rows. Pure functions — every formula comes from @synthetic-platform/stats,
 * every constant and measured number from knobs.json; nothing here is typed
 * twice. Section order mirrors article 8 ("Stats, stress & scale").
 *
 * Sources: docs/designs/2026-07-24-reference-sample-scaling.md (DKW, rare
 * categories, tails, collisions), docs/designs/2026-08-05-source-table-stats.md
 * (entropy, deciles, HLL++, null patterns), ADR 0022/0033/0034/0035.
 */
import {
  birthdayCollisionProb,
  dkwEpsilon,
  expectedCollisions,
  expectedDistinct,
  poolReuse,
  rareCaptureProb,
  tailPoints,
} from "@synthetic-platform/stats";

import { CITES, splitCitation } from "../citations";
import { knob, knobNumber, measured } from "./knobs";

export type StatsTier = "off" | "sample" | "exact";
export type UniquenessMode = "exact" | "exact_chained" | "streaming";

export interface ScenarioInputs {
  /** Source rows. */
  N: number;
  /** Target rows (`--num_rows`). */
  M: number;
  /** Reference sample rows (`--reference_rows_limit`). */
  n: number;
  /** Share of the rarest category that must survive. */
  p: number;
  /** Deepest quantile whose tail matters. */
  q: number;
  /** `--source_stats`. */
  tier: StatsTier;
  /** `--uniqueness_mode`. */
  uniqueness: UniquenessMode;
  /** `--env` (threshold tier). */
  env: string;
  /** Identifier keyspace, as log10 K (12 → 10^12 values). */
  keyspaceLog10: number;
  /** A free-text column's true distinct count (the distinct-truncation card). */
  columnDistinct: number;
  /** Share of that column's rows that are non-empty. */
  nonEmptyShare: number;
  /**
   * A source-value store is attached (the pool layer or `source_values_table`):
   * `_pool_target` then sizes from the filter's cardinality (ADR 0033 D2).
   */
  sourceFilter: boolean;
  /** Time estimate basis: SDK processes per worker (`sdk_containers`). */
  sdkContainers: "single" | "multi";
  /** Time estimate: pools already persisted (warm) or built this run (cold). */
  warm: boolean;
}

export interface ScenarioPreset {
  id: string;
  label: string;
  description: string;
  inputs: ScenarioInputs;
}

/** Code constants the calculator reads (knobs.json, never retyped). */
export const CONSTANTS = {
  poolCap: knobNumber("free_text_pool_max"),
  rowDocCap: knobNumber("max_row_doc_rows"),
  valueChunkCap: knobNumber("max_free_text_values_per_column"),
  topK: knobNumber("rag_top_k"),
  literalMaxDistinct: knobNumber("top_values_max_distinct"),
  keyMargin: knobNumber("fk_key_sample_margin"),
  keyFloor: knobNumber("fk_key_sample_floor"),
  keyCeiling: knobNumber("fk_key_sample_ceiling"),
  defaultN: knobNumber("reference_rows_limit"),
  sourceDomainCap: knobNumber("source_domain_cap"),
} as const;

/** The measured relational pair every performance number stands on (MEASURED "shuffle" block). */
export const MEASURED_PAIR = {
  rowsPerTable: measured("make_throughput_figures.ROWS_PER_TABLE").value as number,
  tables: measured("make_throughput_figures.TABLES").value as number,
};
/** "2 × 10M-row tables", from the MEASURED block. */
export const MEASURED_PAIR_LABEL = `${MEASURED_PAIR.tables} × ${new Intl.NumberFormat("en-US", {
  notation: "compact",
}).format(MEASURED_PAIR.rowsPerTable)}-row tables`;

/** Rows past a quantile a tail estimate needs (design 2026-07-24 §9: "≥ ~20 points"). */
export const TAIL_POINTS_WANTED = 20;
/** α for every DKW band on this tab (95 % confidence). */
export const ALPHA = 0.05;

const BASE: ScenarioInputs = {
  N: 1_000_000,
  M: 90_000_000,
  n: CONSTANTS.defaultN,
  p: 0.001,
  q: 0.999,
  tier: "sample",
  uniqueness: "exact",
  env: "dev",
  keyspaceLog10: 12,
  // ADR 0033 context §2: a 95 %-empty column with 4,022 source-distinct values.
  columnDistinct: 4_022,
  nonEmptyShare: 0.05,
  // Every run attaches a source-value store: run_pipeline sets PipelineConfig.source_values_table
  // to --reference_table (a required flag), and GenerateRecordsDoFn.setup attaches the store from
  // it (the ADR 0023 seam in dofns/generate.py). Not resolve_pool_layer, which runs only under
  // --build_pool_layer.
  sourceFilter: true,
  // The code default (Composer `sdk_containers`); the time card can switch it.
  sdkContainers: knob("sdk_containers").value === "multi" ? "multi" : "single",
  warm: false,
};

export const PRESETS: readonly ScenarioPreset[] = [
  {
    id: "90m-from-1m",
    label: "90M from a 1M source, 10k seed",
    description:
      "Generate 90 million rows from a 1 million-row source through the default 10,000-row reference sample.",
    inputs: BASE,
  },
  {
    id: "exact-tier",
    label: "Same run, exact stats tier",
    description:
      "The 90M run with --source_stats exact: one aggregate scan gives the statistics true distinct counts (and sizes pools first).",
    inputs: { ...BASE, tier: "exact" },
  },
  {
    id: "census",
    label: "Small source (census zone)",
    description: "A 5,000-row source: the 10k sample is the whole table, so marginals carry no sampling error.",
    inputs: { ...BASE, N: 5_000, M: 1_000_000 },
  },
];

export const DEFAULT_PRESET_ID = PRESETS[0]!.id;

export function presetById(id: string | undefined): ScenarioPreset {
  return PRESETS.find((p) => p.id === id) ?? PRESETS[0]!;
}

// ------------------------------------------------------------------ maths --

/** The sample the profiler really sees: a sample larger than its source is the source (census). */
export function effectiveSample(n: number, N: number): { nEff: number; census: boolean } {
  const nEff = Math.max(0, Math.min(n, N));
  return { nEff, census: n >= N };
}

/** Rows needed to see a share-p category with 95 % probability: ln(0.05)/ln(1 − p) ≈ 3/p. */
export function rowsToSee(p: number, confidence = 0.95): number {
  if (p <= 0) return Infinity;
  if (p >= 1) return 1;
  return Math.ceil(Math.log(1 - confidence) / Math.log1p(-p));
}

/**
 * Rows for a share-p category's estimate to reach a 10 % relative standard
 * error, 1/√(np) = 0.1 → n ≈ 100/p (design §3.2). One standard error is
 * ≈ 68 % confidence, not 95 %.
 */
export function rowsToEstimate(p: number): number {
  return p > 0 ? Math.ceil(100 / p) : Infinity;
}

/** Rows needed for `points` sample points past the q-quantile: points/(1 − q). */
export function rowsForTail(q: number, points = TAIL_POINTS_WANTED): number {
  return q < 1 ? Math.ceil(points / (1 - q)) : Infinity;
}

/** Expected distinct values seen in m draws from D equally likely values: D·(1 − (1 − 1/D)^m). */
export function expectedDistinctUniform(D: number, m: number): number {
  if (D <= 0 || m <= 0) return 0;
  return D * expectedDistinct([1 / D], m);
}

/** Share of M uniform draws from a keyspace K that repeat an earlier draw: 1 − (K/M)(1 − e^(−M/K)). */
export function duplicateShare(M: number, K: number): number {
  if (M <= 0 || K <= 0) return 0;
  return 1 - expectedDistinctUniform(K, M) / M;
}

/**
 * Where `_pool_target` takes the column's distinct count from, at the exported
 * commit (engine.py `_pool_target`, ADR 0033 D2): the Tier-2 exact count, else
 * the source filter's cardinality (a source-value store is attached and the
 * column is under the store's cap), else the sample distinct.
 */
export type PoolDistinctSource = "exact stats" | "source filter" | "sample";

export function poolDistinct(
  inputs: Pick<ScenarioInputs, "tier" | "sourceFilter" | "columnDistinct">,
  sampleDistinct: number,
): { distinct: number; via: PoolDistinctSource } {
  if (inputs.tier === "exact") return { distinct: inputs.columnDistinct, via: "exact stats" };
  if (inputs.sourceFilter && inputs.columnDistinct <= CONSTANTS.sourceDomainCap)
    return { distinct: inputs.columnDistinct, via: "source filter" };
  return { distinct: Math.round(sampleDistinct), via: "sample" };
}

/** The b1 pool target: min(num_rows, column distinct, pool cap) (engine.py `_pool_target`). */
export function poolTarget(M: number, distinct: number, cap = CONSTANTS.poolCap): number {
  const bounds = [cap];
  if (M > 0) bounds.push(M);
  if (distinct > 0) bounds.push(distinct);
  return Math.max(Math.min(...bounds), 1);
}

/** Effective rows per element (run_pipeline.py `resolve_batch_size`: toward ~1,000 elements, never below 16). */
export function effectiveBatchSize(requested: number, M: number, defaultBatch = knobNumber("batch_size")): number {
  if (requested !== defaultBatch) return requested;
  if (M <= 0) return defaultBatch;
  return Math.max(defaultBatch, Math.floor(M / 1_000));
}

/** The blocker ratio of an env tier (config/thresholds.yml via knobs.json). */
export function blockerRatio(env: string): number | null {
  try {
    return knobNumber(`blocker_failure_ratio_${env}`);
  } catch {
    return null;
  }
}

// --------------------------------------------------------- time estimate --

/** The measured runs the estimate can stand on (ACCEPT_RUNS, 2026-09-07 acceptance pair + R6). */
export interface TimeBasis {
  index: number;
  label: string;
  startupMin: number;
  poolBranchMin: number;
  generationMin: number;
  dedupLoadMin: number;
  /** Rows the measured phases covered: ROWS_PER_TABLE × TABLES. */
  rows: number;
  /** How the basis run's fleet ramped (ACCEPT_WORKERS_AT_4_MIN): none of them started full. */
  fleetRamp: string;
}

function acceptRun(index: number): TimeBasis {
  const labels = measured("make_throughput_figures.ACCEPT_RUNS").value as string[];
  const phases = measured("make_throughput_figures.ACCEPT_PHASES_MIN").value as Record<string, number[]>;
  const rows =
    (measured("make_throughput_figures.ROWS_PER_TABLE").value as number) *
    (measured("make_throughput_figures.TABLES").value as number);
  const at = (key: string) => {
    const value = phases[key]?.[index];
    if (typeof value !== "number") throw new Error(`ACCEPT_PHASES_MIN["${key}"][${index}] is missing`);
    return value;
  };
  return {
    index,
    label: (labels[index] ?? `run ${index}`).replace(/\n/g, " "),
    startupMin: at("startup (launcher + boot)"),
    poolBranchMin: at("cold pool branch"),
    generationMin: at("generation C + A"),
    dedupLoadMin: at("dedup + load C + A"),
    rows,
    fleetRamp: (measured("make_throughput_figures.ACCEPT_WORKERS_AT_4_MIN").value as string[])[index] ?? "",
  };
}

/**
 * Which measured run matches the settings: exact_chained is the pre-ADR-0034
 * R6 cold run (three barriers); exact is the R7 pair, single or multi SDK
 * process per worker. streaming has no measured run (no barrier): the
 * estimate reuses the matching exact run's generation and flags dedup as unmeasured.
 */
export function timeBasis(uniqueness: UniquenessMode, sdk: "single" | "multi"): TimeBasis {
  if (uniqueness === "exact_chained") return acceptRun(0);
  return acceptRun(sdk === "multi" ? 2 : 1);
}

export interface TimeEstimate {
  basis: TimeBasis;
  startupMin: number;
  poolBranchMin: number;
  generationMin: number;
  /** null when the mode has no measured barrier (streaming). */
  dedupLoadMin: number | null;
  totalMin: number;
  /** M / measured rows: how far the estimate extrapolates. */
  scale: number;
}

export function estimateTime(
  inputs: Pick<ScenarioInputs, "M" | "uniqueness" | "sdkContainers" | "warm">,
): TimeEstimate {
  const basis = timeBasis(inputs.uniqueness, inputs.sdkContainers);
  const scale = inputs.M / basis.rows;
  const generationMin = basis.generationMin * scale;
  const dedupLoadMin = inputs.uniqueness === "streaming" ? null : basis.dedupLoadMin * scale;
  const poolBranchMin = inputs.warm ? 0 : basis.poolBranchMin;
  return {
    basis,
    startupMin: basis.startupMin,
    poolBranchMin,
    generationMin,
    dedupLoadMin,
    totalMin: basis.startupMin + poolBranchMin + generationMin + (dedupLoadMin ?? 0),
    scale,
  };
}

// ----------------------------------------------------------------- outputs --

export interface ScenarioOutputs {
  nEff: number;
  census: boolean;
  /** M/n: generated rows per reference row. */
  ampSample: number;
  /** M/N: generated rows per source row. */
  ampSource: number;
  /** n/N: share of the source the sample reads. */
  sampleShare: number;
  /** DKW ε(n) at α = 0.05 (0 on a census: no sampling error). */
  epsilon: number;
  /** Twice the band: the tightest fidelity threshold that tests the generator, not noise (design §6.2). */
  thresholdFloor: number;
  rareCapture: number;
  rareExpectedRows: number;
  rowsToSee: number;
  rowsToEstimate: number;
  tailPoints: number;
  rowsForTail: number;
  /** Rows per pool value: M / pool target, the target being min(M, D, cap) (code-true). */
  poolReuse: number;
  rowDocShare: number;
  /** Distinct (column, value) chunks embedded from the sample: min(sample distinct, cap). */
  valueChunks: number;
  /** Sample-tier distinct for the column (uniform model: an upper bound) vs the true (exact-tier) count. */
  distinctSample: number;
  distinctExact: number;
  /** The pool target the code resolves, and where its distinct count came from (ADR 0033 D2). */
  poolTarget: number;
  poolDistinctVia: PoolDistinctSource;
  /** What the target would be if only the sample distinct were known (no store, or above its cap). */
  poolTargetSampleOnly: number;
  keyspace: number;
  collisionProb: number;
  expectedCollidingPairs: number;
  /** K for fewer than one expected colliding pair: M(M − 1)/2. */
  keyspaceNeeded: number;
  /** A PK routed to a free-text pool keeps at most `poolCap` distinct values. */
  pkPoolSurvivors: number;
  pkPoolDlq: number;
  /** Duplicate share of random PK draws from a capacity `keyMargin × M` (ADR 0035 sizing). */
  pkDuplicateShareAtMargin: number;
  blockerRatio: number | null;
  blockerRowsAllowed: number | null;
  batchSize: number;
  elements: number;
  time: TimeEstimate;
}

/** The sample tier's distinct for the calculator's column (uniform model: an upper bound, never above n·s). */
export function sampleDistinctFor(
  inputs: Pick<ScenarioInputs, "n" | "N" | "nonEmptyShare" | "columnDistinct">,
): number {
  const nonEmptySample = effectiveSample(inputs.n, inputs.N).nEff * inputs.nonEmptyShare;
  return Math.min(expectedDistinctUniform(inputs.columnDistinct, nonEmptySample), nonEmptySample);
}

export function computeScenario(inputs: ScenarioInputs): ScenarioOutputs {
  const { N, M, n, p, q } = inputs;
  const { nEff, census } = effectiveSample(n, N);
  const epsilon = census ? 0 : dkwEpsilon(nEff, ALPHA);
  const keyspace = 10 ** inputs.keyspaceLog10;
  const distinctSample = sampleDistinctFor(inputs);
  const distinctExact = inputs.columnDistinct;
  const resolved = poolDistinct(inputs, distinctSample);
  const target = poolTarget(M, resolved.distinct);
  const ratio = blockerRatio(inputs.env);
  const batchSize = effectiveBatchSize(knobNumber("batch_size"), M);
  return {
    nEff,
    census,
    ampSample: nEff > 0 ? M / nEff : Infinity,
    ampSource: N > 0 ? M / N : Infinity,
    sampleShare: N > 0 ? Math.min(1, n / N) : 0,
    epsilon,
    thresholdFloor: 2 * epsilon,
    rareCapture: census ? 1 : rareCaptureProb(p, nEff),
    rareExpectedRows: nEff * p,
    rowsToSee: rowsToSee(p),
    rowsToEstimate: rowsToEstimate(p),
    tailPoints: tailPoints(nEff, q),
    rowsForTail: rowsForTail(q),
    poolReuse: poolReuse(M, target),
    rowDocShare: nEff > 0 ? Math.min(1, CONSTANTS.rowDocCap / nEff) : 0,
    valueChunks: Math.min(Math.round(distinctSample), CONSTANTS.valueChunkCap),
    distinctSample,
    distinctExact,
    poolTarget: target,
    poolDistinctVia: resolved.via,
    poolTargetSampleOnly: poolTarget(M, Math.round(distinctSample)),
    keyspace,
    collisionProb: birthdayCollisionProb(M, keyspace),
    expectedCollidingPairs: expectedCollisions(M, keyspace),
    keyspaceNeeded: (M * (M - 1)) / 2,
    pkPoolSurvivors: Math.min(M, CONSTANTS.poolCap),
    pkPoolDlq: Math.max(0, M - CONSTANTS.poolCap),
    pkDuplicateShareAtMargin: duplicateShare(M, CONSTANTS.keyMargin * M),
    blockerRatio: ratio,
    blockerRowsAllowed: ratio === null ? null : Math.floor(ratio * M),
    batchSize,
    elements: batchSize > 0 ? Math.ceil(M / batchSize) : 0,
    time: estimateTime(inputs),
  };
}

// --------------------------------------------------------- recommendations --

export interface Recommendation {
  id: string;
  tone: "warn" | "info";
  /** What to set, or what to know. */
  title: string;
  why: string;
  knob?: string;
  cite: { label: string; path: string; line?: number };
}

const SCALING = "docs/designs/2026-07-24-reference-sample-scaling.md";

/** Rule-based advice; every rule cites the doc or code it comes from. */
export function recommend(inputs: ScenarioInputs, out: ScenarioOutputs): Recommendation[] {
  const recs: Recommendation[] = [];
  if (out.census) {
    recs.push({
      id: "census",
      tone: "info",
      title: "The sample is the whole source (census)",
      why: `n = ${fmt(inputs.n)} ≥ N = ${fmt(inputs.N)}: marginals carry no sampling error, but a small source is the leak-risk zone — keep memorization caps tight.`,
      knob: "reference_rows_limit",
      cite: { label: "Reference-sample scaling §8 (per-table policy)", path: SCALING },
    });
  }
  if (!out.census && inputs.n < out.rowsToSee) {
    recs.push({
      id: "rare",
      tone: "warn",
      title: `Raise --reference_rows_limit to ≥ ${fmt(out.rowsToSee)} if a ${pct(inputs.p)} category must survive`,
      why: `At n = ${fmt(out.nEff)} a share-${pct(inputs.p)} category appears with probability ${pct(out.rareCapture)}; a category absent from the sample is absent from all ${fmt(inputs.M)} generated rows.`,
      knob: "reference_rows_limit",
      cite: { label: "Reference-sample scaling §3.2 (n ≈ 3/p)", path: SCALING },
    });
  }
  if (!out.census && out.tailPoints < TAIL_POINTS_WANTED) {
    recs.push({
      id: "tail",
      tone: "warn",
      title: `The q = ${inputs.q} tail rests on ${fmt(out.tailPoints)} points`,
      why: `Want ≥ ${TAIL_POINTS_WANTED} points past the deepest quantile you care about: n ≥ ${fmt(out.rowsForTail)} — or keep n = ${fmt(inputs.n)} and profile that column over the full table.`,
      knob: "reference_rows_limit",
      cite: { label: "Reference-sample scaling §3.3 and §9", path: SCALING },
    });
  }
  if (inputs.tier !== "exact") {
    const starved =
      out.poolDistinctVia === "sample" && out.poolTargetSampleOnly < poolTarget(inputs.M, out.distinctExact);
    recs.push({
      id: "exact-tier",
      tone: starved ? "warn" : "info",
      title: starved
        ? "Use --source_stats exact: this pool is sized from the sample"
        : "Use --source_stats exact for true distinct counts in the statistics",
      why: starved
        ? `No source-value store sizes this column (${inputs.sourceFilter ? `its ${fmt(out.distinctExact)} distinct values exceed the store's ${fmt(CONSTANTS.sourceDomainCap)} cap` : "none is attached"}), so _pool_target falls back to the sample distinct: ${fmt(out.poolTargetSampleOnly)} instead of ${fmt(poolTarget(inputs.M, out.distinctExact))}. The exact tier's HLL++ count comes first.`
        : `Pools are already sized from the source filter's cardinality (${fmt(out.poolTarget)}; ADR 0033 D2), but source_table_stats keeps the sample's truncated distinct (≈ ${fmt(out.distinctSample)} of ${fmt(out.distinctExact)}). One HLL++ scan gives the statistics the true count.`,
      knob: "source_stats",
      cite: {
        label: "ADR 0033 D2 (pool target: exact → source filter → sample)",
        ...splitCitation(CITES.adr0033D2.source),
      },
    });
  }
  if (out.poolReuse > 1) {
    recs.push({
      id: "pool-reuse",
      tone: "info",
      title: `Each pool value repeats ≈ ${fmt(out.poolReuse)} times`,
      why: `A free-text pool holds at most ${CONSTANTS.poolCap} values, so ${fmt(inputs.M)} rows reuse them. Keep --freetext_expansion identifiers (default) or all for code-like columns; pools never emit identifiers.`,
      knob: "freetext_expansion",
      cite: { label: "Free-text expansion modes", path: "docs/designs/2026-08-05-freetext-expansion-modes.md" },
    });
  }
  if (out.collisionProb > 0.01) {
    recs.push({
      id: "keyspace",
      tone: "warn",
      title: `Widen identifier keyspaces past ${sci(out.keyspaceNeeded)}`,
      why: `${fmt(inputs.M)} identifiers from 10^${inputs.keyspaceLog10} values collide with probability ${pct(out.collisionProb)} (≈ ${fmt(out.expectedCollidingPairs)} pairs). Fewer than one expected pair needs K > M²/2.`,
      cite: { label: "Reference-sample scaling §6.1", path: SCALING },
    });
  }
  if (inputs.n > 100_000) {
    recs.push({
      id: "driver-ceiling",
      tone: "warn",
      title: "Samples above ~100k rows do not fit the driver path",
      why: "Reference rows load driver-side into beam.Create; the practical ceiling is 50–100k wide rows before driver memory and the job-graph size limit bite.",
      knob: "reference_rows_limit",
      cite: { label: "Reference-sample scaling §7 and §8", path: SCALING },
    });
  }
  if (inputs.uniqueness === "streaming") {
    recs.push({
      id: "streaming",
      tone: "warn",
      title: "Streaming lands duplicates",
      why: "streaming measures the duplicate rate instead of removing it; a failing run is still FAILED_BLOCKER, so re-run with --write_disposition=overwrite.",
      knob: "uniqueness_mode",
      cite: { label: "--uniqueness_mode help (run_pipeline.py)", path: sourcePath("uniqueness_mode") },
    });
  }
  if (inputs.sdkContainers === "single") {
    const multi = estimateTime({ ...inputs, sdkContainers: "multi" });
    recs.push({
      id: "sdk-multi",
      tone: "info",
      title: `sdk_containers=multi: ≈ ${fmt(out.time.totalMin - multi.totalMin)} min faster at the measured rates`,
      why: "One Python interpreter per worker is the generation ceiling (the GIL); multi runs one SDK process per vCPU. The saving extrapolates the R7 pair's measured phases.",
      knob: "sdk_containers",
      cite: { label: "ADR 0034", path: "docs/adr/0034-generation-throughput-single-barrier-shared-engines.md" },
    });
  }
  recs.push({
    id: "fleet",
    tone: "info",
    title: "Start the fleet full: --initial_workers = max workers, --autoscaling fixed",
    why: `With initial_workers empty the R6 pair started on 2 workers and C_TABLE ran 8 min at a quarter of its steady rate; the time estimate's own basis (${out.time.basis.label}) ramped ${out.time.basis.fleetRamp}, so a full fleet should beat it. Throughput autoscaling lost ~4 min per job to scale-downs.`,
    knob: "initial_workers",
    cite: { label: "--initial_workers / --autoscaling help, ADR 0034", path: sourcePath("initial_workers") },
  });
  recs.push({
    id: "noise-floor",
    tone: "info",
    title: `Keep fidelity thresholds ≥ ${out.census ? "0 (census)" : out.thresholdFloor.toFixed(4)}`,
    why: "A threshold tighter than about twice the DKW band tests sampling noise, not the generator. Full-table stats remove the floor.",
    cite: { label: "Reference-sample scaling §6.2", path: SCALING },
  });
  return recs;
}

/** A knob's source file (the recommendation links the file; the knob sheet has the line). */
function sourcePath(id: string): string {
  return knob(id).source.replace(/:\d+$/, "");
}

// ------------------------------------------------------------ formatting --

function fmt(v: number): string {
  if (!Number.isFinite(v)) return "∞";
  if (Math.abs(v) >= 100) return Math.round(v).toLocaleString("en-US");
  return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
}
function pct(v: number): string {
  return `${(v * 100).toLocaleString("en-US", { maximumFractionDigits: v < 0.01 ? 3 : 1 })}%`;
}
function sci(v: number): string {
  if (!Number.isFinite(v) || v <= 0) return String(v);
  const e = Math.floor(Math.log10(v));
  return `${(v / 10 ** e).toFixed(2)}·10^${e}`;
}
export const formatSci = sci;
