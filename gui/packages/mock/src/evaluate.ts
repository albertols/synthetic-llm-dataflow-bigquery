/**
 * The mock evaluator: turns one table's source and synthetic rows into
 * `evaluation_metrics`, `evaluation_profiles` and `evaluation_row_flags` rows.
 *
 * Nothing here is typed as a result. Profiles are counts drawn at the stated n
 * from the rows' distributions, every metric value is a `packages/stats`
 * function of those profiles (or of the rows), noise floors come from the same
 * package, and status, score and the detail notes are what the evaluator's
 * `to_metric_row` would write (`scoreRow`, the golden-pinned port of
 * `sdfb_evaluation.scoring`). Interval-method metrics carry their CI (Wilson,
 * Newcombe — folded for an absolute difference — and DeLong) and no scalar
 * floor, as the evaluator's producers do (Ruling R41). A row of a table read
 * as a row sample says `method = sample` with its rate, never `exact` (Ruling
 * R72). `mock.storyline.test.ts` recomputes metrics from the profiles.
 */
import {
  catalogueById,
  LIFT_COUNT_KEYS,
  type CatalogueMetric,
  type EvaluationMetricsRow,
  type EvaluationProfilesRow,
  type EvaluationRowFlagsRow,
  type MetricId,
} from "@synthetic-platform/contracts";
import {
  aucInterval,
  binormalRoc,
  cohensW,
  contingencyTvd,
  cramersV,
  decileKsLegacy,
  entropyBits,
  equiprobableEdges,
  expectedDistinct,
  fisherZDeltaFloor,
  foldedAbsInterval,
  histogram,
  jsdBits,
  jsdNullExpectationBits,
  ksBracket,
  ksCritical,
  miBiasNats,
  newcombe,
  nmi,
  normalize,
  pearson,
  pitW1,
  profilerDeciles,
  psi,
  quantiles,
  type Random,
  rateRatio,
  scoreRow,
  sha256Hex,
  spearman,
  tvd,
  tvdNullExpectation,
  w1FromBins,
  wilson,
} from "@synthetic-platform/stats";

import type { EvalSpec } from "./storyline";
import { epoch, FREE_TEXT_POOL_MAX, textUniverse, type Pool } from "./synth";
import type { ColumnDef, Row, TableDef, Value } from "./thelook";

export const HIST_BINS = 20;
/** Every decile plus the quartiles and the 1 / 5 / 95 / 99 % tails (QQ plots, decile tables). */
const PROBS = [0, 0.01, 0.05, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 0.95, 0.99, 1];
/** The D6 literal policy: literal only with ≤ 50 source-distinct values and a source count ≥ 10. */
const LITERAL_MAX_DISTINCT = 50;
const K_ANON = 10;
const CHANCE_MATCH = 2e-6;

export interface TableContext {
  spec: EvalSpec;
  table: TableDef;
  evaluatedAt: string;
  landingTable: string | null;
  sourceTable: string | null;
  source: readonly Row[];
  synthetic: readonly Row[];
  /** R and H: disjoint halves of the source sample (the reference and its holdout). */
  reference: readonly Row[];
  holdout: readonly Row[];
  pools: ReadonlyMap<string, Pool>;
  /** Rows each metric covers: the table (exact) or the sampled rows (sampled mode). */
  nSource: number;
  nSynthetic: number;
  /** Rows actually in the landing table (the synthetic side of row_count_ratio). */
  rowsSynthetic: number;
  rowsExpected: number;
  nReference: number;
  sampleRate: number | null;
  encodingPlanDigest: string;
  referenceVerified: boolean;
  /** Large per-column draws of finite-universe free-text columns (distinct and entropy at n). */
  textDraws: ReadonlyMap<string, string[]>;
  /** Stats-snapshot null fractions the run profiled (for column.source_stats_drift). */
  profiledNullFraction: ReadonlyMap<string, number>;
  rng: Random;
}

export interface TableResult {
  metrics: EvaluationMetricsRow[];
  profiles: EvaluationProfilesRow[];
  flags: EvaluationRowFlagsRow[];
  /** Per-column distances the detection model sees. */
  columnDistances: number[];
}

interface Reading {
  column?: ColumnDef;
  column2?: string;
  edge?: string;
  value: number | null;
  sourceValue?: number | null;
  syntheticValue?: number | null;
  baseline?: number | null;
  noiseFloor?: number | null;
  ciLow?: number | null;
  ciHigh?: number | null;
  nSource?: number | null;
  nSynthetic?: number | null;
  method?: EvaluationMetricsRow["method"];
  sampleRate?: number | null;
  detail?: Record<string, unknown> | null;
  featureSetDigest?: string | null;
  /** false on a documented foreign-key edge: its orphan rate is INFO (Ruling R42). */
  enforced?: boolean;
}

/** A Wilson interval as a reading's CI (the producer contract of the `wilson` noise method). */
export function wilsonCi(k: number, n: number): { ciLow: number; ciHigh: number } {
  const [ciLow, ciHigh] = wilson(k, n);
  return { ciLow, ciHigh };
}

/** |p1 − p2|'s CI: Newcombe's interval for p1 − p2, folded (the `newcombe` noise method). */
export function foldedNewcombeCi(k1: number, n1: number, k2: number, n2: number): { ciLow: number; ciHigh: number } {
  const [ciLow, ciHigh] = foldedAbsInterval(...newcombe(k1, n1, k2, n2));
  return { ciLow, ciHigh };
}

const MEMO = new WeakMap<object, Map<string, unknown>>();
/** Source-side work repeats across evaluations (same rows array): compute it once per (rows, key). */
function memo<T>(owner: object, key: string, compute: () => T): T {
  let map = MEMO.get(owner);
  if (!map) MEMO.set(owner, (map = new Map<string, unknown>()));
  if (!map.has(key)) map.set(key, compute());
  return map.get(key) as T;
}

/**
 * A lift's reading as the evaluator's `rate_ratio` returns it (Ruling R38): with no copies on
 * either side the ratio is undefined (None) and the interval (0, ∞); with copies only in R the
 * ratio is +∞ — both store value NULL (json_safe) while status gates on ci_low. No zero
 * correction: the point estimate is what Python writes, not a smoothed stand-in. The two event
 * counts go to the detail under the producer's own keys (`LIFT_COUNT_KEYS`).
 */
type LiftId = keyof typeof LIFT_COUNT_KEYS;

function liftReading(id: LiftId, m1: number, t1: number, m2: number, t2: number) {
  const r = rateRatio(m1, t1, m2, t2, 0.05);
  const value = m1 + m2 === 0 ? null : m2 === 0 ? Number.POSITIVE_INFINITY : r.ratio;
  const [reference, holdout] = LIFT_COUNT_KEYS[id];
  return { value, ciLow: r.lo, ciHigh: r.hi, detail: { [reference]: m1, [holdout]: m2 } };
}

/** detail.reason for a pair statistic that is undefined on a side (a constant column there). */
function undefinedOn(statistic: string, source: number | null, synthetic: number | null) {
  if (source !== null && synthetic !== null) return null;
  const side =
    source === null && synthetic === null ? "both sides" : source === null ? "the source" : "the synthetic side";
  return { reason: `${statistic} is undefined on ${side}: a column of the pair has one observed level there` };
}

const round = (v: number | null | undefined, digits = 12) =>
  v === null || v === undefined || !Number.isFinite(v) ? null : Number(v.toPrecision(digits));
/** Twelve significant digits for a finite number; ±Infinity and NaN pass through (the scorer grades them). */
const roundKeep = (v: number | null | undefined) =>
  v === null || v === undefined ? null : Number.isFinite(v) ? Number(v.toPrecision(12)) : v;

export class TableEvaluator {
  readonly result: TableResult = { metrics: [], profiles: [], flags: [], columnDistances: [] };

  constructor(readonly ctx: TableContext) {}

  metric(id: MetricId, reading: Reading): EvaluationMetricsRow {
    const catalogue: CatalogueMetric = catalogueById[id];
    // Staged (storyline): this metric arrives without its noise input, as from a producer that
    // did not record it — the scorer keeps a WARN/FAIL and says the check was unavailable.
    const gap = this.ctx.spec.staged?.withoutNoiseInput === id && !catalogue.uses_ci_bound;
    if (gap) reading = { ...reading, noiseFloor: null, ciLow: null, ciHigh: null };
    // The producer's MetricValue (twelve significant digits), scored as to_metric_row does.
    const scored = scoreRow(
      catalogue,
      {
        value: roundKeep(reading.value),
        ciLow: roundKeep(reading.ciLow),
        ciHigh: roundKeep(reading.ciHigh),
        noiseFloor: roundKeep(reading.noiseFloor),
        sourceValue: roundKeep(reading.sourceValue),
        detail: reading.detail ?? null,
        columnKind: reading.column?.kind ?? null,
      },
      { enforced: reading.enforced ?? true },
    );
    const { sampleRate } = this.ctx;
    const rowSample = sampleRate !== null && sampleRate < 1;
    const row: EvaluationMetricsRow = {
      evaluation_id: this.ctx.spec.id,
      evaluated_at: this.ctx.evaluatedAt,
      table_name: this.ctx.table.name,
      landing_table: this.ctx.landingTable,
      source_table: this.ctx.sourceTable,
      level: catalogue.level,
      family: catalogue.family,
      metric_id: id,
      metric_version: catalogue.version,
      value_kind: catalogue.value_kind as EvaluationMetricsRow["value_kind"],
      column_name: reading.column?.name ?? null,
      column_name_2: reading.column2 ?? null,
      column_kind: reading.column?.kind ?? null,
      edge: reading.edge ?? null,
      value: scored.value,
      source_value: scored.source_value,
      synthetic_value: round(reading.syntheticValue),
      baseline_value: catalogue.baseline ? round(reading.baseline) : null,
      score: scored.score,
      status: scored.status,
      threshold_warn: catalogue.thresholds.warn,
      threshold_fail: catalogue.thresholds.fail,
      noise_floor: scored.noise_floor,
      noise_floor_method:
        catalogue.noise_floor === "none" ? null : (catalogue.noise_floor as EvaluationMetricsRow["noise_floor_method"]),
      ci_low: scored.ci_low,
      ci_high: scored.ci_high,
      n_source: reading.nSource === undefined ? this.ctx.nSource : reading.nSource,
      n_synthetic: reading.nSynthetic === undefined ? this.ctx.nSynthetic : reading.nSynthetic,
      method:
        reading.method ??
        (rowSample ? "sample" : (catalogue.estimator.split("/")[0] as EvaluationMetricsRow["method"])),
      sample_rate: reading.sampleRate === undefined ? this.ctx.sampleRate : reading.sampleRate,
      encoding_plan_digest: this.ctx.encodingPlanDigest,
      feature_set_digest: reading.featureSetDigest ?? null,
      detail: scored.detail as EvaluationMetricsRow["detail"],
    };
    this.result.metrics.push(row);
    return row;
  }

  notEvaluated(id: MetricId, reason: string, extra: Partial<Reading> = {}) {
    return this.metric(id, { value: null, detail: { reason }, ...extra });
  }

  profile(
    kind: EvaluationProfilesRow["profile_kind"],
    side: EvaluationProfilesRow["side"],
    payload: Record<string, unknown>,
    options: {
      column?: string | null;
      edge?: string | null;
      n?: number | null;
      truncated?: boolean;
      edges?: number[];
    } = {},
  ) {
    this.result.profiles.push({
      evaluation_id: this.ctx.spec.id,
      evaluated_at: this.ctx.evaluatedAt,
      table_name: this.ctx.table.name,
      column_name: options.column ?? null,
      edge: options.edge ?? null,
      profile_kind: kind,
      side,
      n: options.n ?? null,
      truncated: options.truncated ?? false,
      edges_digest: options.edges ? sha256Hex(JSON.stringify(options.edges)).slice(0, 16) : null,
      payload: payload as EvaluationProfilesRow["payload"],
    });
  }

  // -------------------------------------------------------------- columns --

  values(rows: readonly Row[], column: string): Value[] {
    return memo(rows, `values:${column}`, () => rows.map((r) => r[column] ?? null));
  }

  /** Counts drawn at n from the shares a sample shows. */
  countsAt(shares: readonly number[], n: number): number[] {
    return this.ctx.rng.multinomial(n, normalize(shares));
  }

  nullAndEmpty(column: ColumnDef) {
    const { nSource, nSynthetic, rng } = this.ctx;
    const src = this.values(this.ctx.source, column.name);
    const syn = this.values(this.ctx.synthetic, column.name);
    const shareOf = (vs: Value[], pred: (v: Value) => boolean) => vs.filter(pred).length / Math.max(vs.length, 1);
    const kSrc = rng.binomial(
      nSource,
      shareOf(src, (v) => v === null),
    );
    const kSyn = rng.binomial(
      nSynthetic,
      shareOf(syn, (v) => v === null),
    );
    this.metric("column.null_rate_delta", {
      column,
      value: Math.abs(kSyn / nSynthetic - kSrc / nSource),
      sourceValue: kSrc / nSource,
      syntheticValue: kSyn / nSynthetic,
      baseline: Math.abs(rng.binomial(this.ctx.nReference, kSrc / nSource) / this.ctx.nReference - kSrc / nSource),
      ...foldedNewcombeCi(kSyn, nSynthetic, kSrc, nSource),
    });
    if (column.bqType === "STRING" && column.kind !== "identifier" && column.role === undefined) {
      const eSrc = rng.binomial(
        nSource,
        shareOf(src, (v) => v === ""),
      );
      const eSyn = rng.binomial(
        nSynthetic,
        shareOf(syn, (v) => v === ""),
      );
      this.metric("column.empty_rate_delta", {
        column,
        value: Math.abs(eSyn / nSynthetic - eSrc / nSource),
        sourceValue: eSrc / nSource,
        syntheticValue: eSyn / nSynthetic,
        baseline: Math.abs(rng.binomial(this.ctx.nReference, eSrc / nSource) / this.ctx.nReference - eSrc / nSource),
        ...foldedNewcombeCi(eSyn, nSynthetic, eSrc, nSource),
      });
    }
  }

  typeValidity(column: ColumnDef) {
    const syn = this.values(this.ctx.synthetic, column.name).filter((v) => v !== null);
    const valid = (v: Value) =>
      column.bqType === "STRING"
        ? typeof v === "string"
        : column.bqType === "BOOL"
          ? typeof v === "boolean"
          : column.bqType === "TIMESTAMP"
            ? typeof v === "number" || (typeof v === "string" && !Number.isNaN(Date.parse(v)))
            : typeof v === "number";
    const share = syn.filter(valid).length / Math.max(syn.length, 1);
    const k = this.ctx.rng.binomial(this.ctx.nSynthetic, share);
    // The catalogue names no noise method for type validity: no floor, no CI (like the evaluator).
    this.metric("field.type_validity", { column, value: k / this.ctx.nSynthetic });
  }

  sourceStatsDrift(column: ColumnDef) {
    const profiled = this.ctx.profiledNullFraction.get(column.name);
    const src = this.values(this.ctx.source, column.name);
    const actual = src.filter((v) => v === null).length / Math.max(src.length, 1);
    this.metric("column.source_stats_drift", {
      column,
      value: profiled === undefined ? null : Math.abs(profiled - actual),
      sourceValue: actual,
      syntheticValue: profiled ?? null,
      detail:
        profiled === undefined
          ? { reason: "no source_table_stats snapshot for this reference digest" }
          : { field: "null_fraction" },
    });
  }

  numeric(column: ColumnDef, isTime: boolean) {
    const { nSource, nSynthetic, nReference, rng } = this.ctx;
    const toNumber = (v: Value) =>
      v === null || typeof v === "boolean" ? null : isTime ? epoch(v) : typeof v === "number" ? v : null;
    const srcAll = memo(this.ctx.source, `num:${column.name}`, () =>
      this.values(this.ctx.source, column.name).map(toNumber),
    );
    const synAll = this.values(this.ctx.synthetic, column.name).map(toNumber);
    const src = memo(this.ctx.source, `finite:${column.name}`, () =>
      srcAll.filter((v): v is number => v !== null && Number.isFinite(v)),
    );
    const syn = synAll.filter((v): v is number => v !== null && Number.isFinite(v));
    const ref = memo(this.ctx.reference, `finite:${column.name}`, () =>
      this.values(this.ctx.reference, column.name)
        .map(toNumber)
        .filter((v): v is number => v !== null),
    );
    const unit = isTime ? "epoch_seconds" : "value";
    if (!src.length || !syn.length) {
      for (const id of ["column.ks", "column.pit_w1", "column.jsd"] as const)
        this.notEvaluated(id, "no non-null values", { column });
      return;
    }
    const edges = memo(this.ctx.source, `edges:${column.name}`, () => equiprobableEdges(src, HIST_BINS));
    const srcHist = memo(this.ctx.source, `hist:${column.name}`, () => histogram(src, edges));
    const cs = this.countsAt(srcHist, nSource);
    const cy = this.countsAt(histogram(syn, edges), nSynthetic);
    const cr = this.countsAt(srcHist, nReference);
    const min = Math.min(...src);
    const max = Math.max(...src);
    for (const [side, counts, values, n] of [
      ["source", cs, src, nSource],
      ["synthetic", cy, syn, nSynthetic],
      ["reference", cr, ref, nReference],
    ] as const) {
      const nulls =
        side === "reference"
          ? 0
          : rng.binomial(n, (side === "source" ? srcAll : synAll).filter((v) => v === null).length / srcAll.length);
      this.profile(
        "histogram",
        side,
        {
          edges,
          counts,
          min: values.length ? Math.min(...values) : null,
          max: values.length ? Math.max(...values) : null,
          nulls,
          unit,
        },
        { column: column.name, n, edges },
      );
    }
    for (const [side, values, n] of [
      ["source", src, nSource],
      ["synthetic", syn, nSynthetic],
    ] as const) {
      this.profile(
        "quantiles",
        side,
        { probs: PROBS, values: quantiles(values, PROBS), unit },
        { column: column.name, n },
      );
      const mean = values.reduce((a, b) => a + b, 0) / values.length;
      const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(values.length - 1, 1);
      const sd = Math.sqrt(variance);
      const m3 = values.reduce((a, b) => a + (b - mean) ** 3, 0) / values.length;
      const m4 = values.reduce((a, b) => a + (b - mean) ** 4, 0) / values.length;
      this.profile(
        "moments",
        side,
        {
          n,
          mean,
          std: sd,
          skewness: sd > 0 ? m3 / sd ** 3 : null,
          kurtosis_excess: sd > 0 ? m4 / sd ** 4 - 3 : null,
          min: Math.min(...values),
          max: Math.max(...values),
          zeros: Math.round((values.filter((v) => v === 0).length / values.length) * n),
          unit,
        },
        { column: column.name, n },
      );
    }
    const bracket = ksBracket(cs, cy)!;
    const baseKs = ksBracket(cs, cr)!;
    this.metric("column.ks", {
      column,
      value: bracket.dLo,
      baseline: baseKs.dLo,
      noiseFloor: ksCritical(nSource, nSynthetic),
      detail: { d_lo: bracket.dLo, d_hi: bracket.dHi },
    });
    this.result.columnDistances.push(bracket.dLo);
    this.metric("column.pit_w1", { column, value: pitW1(cs, cy), baseline: pitW1(cs, cr) });
    this.metric("column.wasserstein", {
      column,
      value: w1FromBins(edges, cs, cy),
      baseline: w1FromBins(edges, cs, cr),
    });
    if (!isTime)
      this.metric("column.decile_ks_legacy", {
        column,
        value: decileKsLegacy(
          memo(src, "deciles", () => profilerDeciles(src)),
          profilerDeciles(syn),
        ),
        baseline: decileKsLegacy(
          memo(src, "deciles", () => profilerDeciles(src)),
          memo(ref, "deciles", () => profilerDeciles(ref)),
        ),
      });
    this.metric("column.jsd", {
      column,
      value: jsdBits(cs, cy),
      baseline: jsdBits(cs, cr),
      noiseFloor: jsdNullExpectationBits(cs.length, nSource, nSynthetic),
    });
    this.metric("column.psi", { column, value: psi(cs, cy), baseline: psi(cs, cr) });
    const stats = (values: number[]) => {
      const mean = values.reduce((a, b) => a + b, 0) / values.length;
      const sd = Math.sqrt(values.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(values.length - 1, 1));
      return { mean, sd };
    };
    const s = stats(src);
    const y = stats(syn);
    const r = stats(ref.length ? ref : src);
    const pooled = (a: { sd: number }, b: { sd: number }) => Math.sqrt((a.sd ** 2 + b.sd ** 2) / 2) || 1;
    this.metric("column.smd", {
      column,
      value: Math.abs(y.mean - s.mean) / pooled(s, y),
      sourceValue: s.mean,
      syntheticValue: y.mean,
      baseline: Math.abs(r.mean - s.mean) / pooled(s, r),
    });
    // Staged (storyline): a column the source holds constant in this scope while the synthetic
    // side varies — the ratio is +∞, past the bad side of a target metric: FAIL, nonfinite (R43).
    const staged = this.ctx.spec.staged?.spreadFromConstant;
    const sourceSd = staged && staged.table === this.ctx.table.name && staged.column === column.name ? 0 : s.sd;
    this.metric("column.std_ratio", {
      column,
      value: sourceSd > 0 ? y.sd / sourceSd : y.sd > 0 ? Number.POSITIVE_INFINITY : null,
      sourceValue: sourceSd,
      syntheticValue: y.sd,
      baseline: sourceSd > 0 ? r.sd / sourceSd : null,
    });
    if (!isTime) {
      const zs = rng.binomial(nSource, src.filter((v) => v === 0).length / src.length);
      const zy = rng.binomial(nSynthetic, syn.filter((v) => v === 0).length / syn.length);
      this.metric("column.zero_rate_delta", {
        column,
        value: Math.abs(zy / nSynthetic - zs / nSource),
        baseline: Math.abs(rng.binomial(nReference, zs / nSource) / nReference - zs / nSource),
        ...foldedNewcombeCi(zy, nSynthetic, zs, nSource),
      });
    }
    const occupied = histogram(syn, edges).filter((c) => c > 0).length;
    const sourceOccupied = histogram(src, edges).filter((c) => c > 0).length;
    this.metric("column.range_coverage", {
      column,
      value: occupied / sourceOccupied,
      baseline: histogram(ref, edges).filter((c) => c > 0).length / sourceOccupied,
      method: "binned",
    });
    const inRange = syn.filter((v) => v >= min && v <= max).length / syn.length;
    const k = rng.binomial(nSynthetic, inRange);
    this.metric("field.range_adherence", {
      column,
      value: k / nSynthetic,
      ...wilsonCi(k, nSynthetic),
    });
    if (isTime) this.temporalMix(column, src, syn);
  }

  temporalMix(column: ColumnDef, src: number[], syn: number[]) {
    const { nSource, nSynthetic, nReference } = this.ctx;
    const mix = (values: number[]) => {
      const dow = new Array<number>(7).fill(0);
      const month = new Array<number>(12).fill(0);
      const hour = new Array<number>(24).fill(0);
      for (const t of values) {
        const d = new Date(t * 1000);
        dow[(d.getUTCDay() + 6) % 7]! += 1;
        month[d.getUTCMonth()]! += 1;
        hour[d.getUTCHours()]! += 1;
      }
      return { dow, month, hour };
    };
    const ms = memo(src, "temporal-mix", () => mix(src));
    const my = mix(syn);
    const cs = {
      dow: this.countsAt(ms.dow, nSource),
      month: this.countsAt(ms.month, nSource),
      hour: this.countsAt(ms.hour, nSource),
    };
    const cy = {
      dow: this.countsAt(my.dow, nSynthetic),
      month: this.countsAt(my.month, nSynthetic),
      hour: this.countsAt(my.hour, nSynthetic),
    };
    this.profile("temporal_mix", "source", { ...cs, day_granularity: false }, { column: column.name, n: nSource });
    this.profile(
      "temporal_mix",
      "synthetic",
      { ...cy, day_granularity: false },
      { column: column.name, n: nSynthetic },
    );
    for (const key of ["dow", "month", "hour"] as const) {
      const cr = this.countsAt(ms[key], nReference);
      this.metric(`column.${key}_tvd`, {
        column,
        value: tvd(cs[key], cy[key]),
        baseline: tvd(cs[key], cr),
        noiseFloor: tvdNullExpectation(cs[key], nSource, nSynthetic),
      });
    }
  }

  categorical(column: ColumnDef) {
    const { nSource, nSynthetic, nReference } = this.ctx;
    const key = (v: Value) => (v === null ? null : String(v));
    const srcValues = this.values(this.ctx.source, column.name)
      .map(key)
      .filter((v): v is string => v !== null && v !== "");
    const synValues = this.values(this.ctx.synthetic, column.name)
      .map(key)
      .filter((v): v is string => v !== null && v !== "");
    const categories = [...new Set([...srcValues, ...synValues])].sort();
    const sourceSet = new Set(srcValues);
    const shares = (values: string[]) => categories.map((c) => values.filter((v) => v === c).length);
    const ps = shares(srcValues);
    const py = shares(synValues);
    const cs = this.countsAt(ps, nSource);
    const cy = this.countsAt(py, nSynthetic);
    const cr = this.countsAt(ps, nReference);
    const m = Math.min(nSource, nSynthetic);
    const csM = this.countsAt(ps, m);
    const cyM = this.countsAt(py, m);
    const topk = (counts: number[], n: number) => {
      const total = counts.reduce((a, b) => a + b, 0) || 1;
      const order = counts.map((c, i) => [c, i] as const).sort((a, b) => b[0] - a[0]);
      const items = order.slice(0, 10).map(([c, i]) => {
        const label = categories[i]!;
        const literal = categories.length <= LITERAL_MAX_DISTINCT && sourceSet.has(label) && (cs[i] ?? 0) >= K_ANON;
        return {
          label: literal ? label : `h:${sha256Hex(`${column.name}\u001f${label}`).slice(0, 8)}`,
          literal,
          count: c,
          share: c / total,
        };
      });
      return {
        items,
        other_count: total - items.reduce((a, b) => a + b.count, 0),
        distinct: counts.filter((c) => c > 0).length,
        total: n,
        nulls: 0,
      };
    };
    this.profile("topk", "source", topk(cs, nSource), {
      column: column.name,
      n: nSource,
      truncated: categories.length > 10,
    });
    this.profile("topk", "synthetic", topk(cy, nSynthetic), {
      column: column.name,
      n: nSynthetic,
      truncated: categories.length > 10,
    });
    const inVocabulary = categories.reduce((acc, c, i) => acc + (sourceSet.has(c) ? cy[i]! : 0), 0);
    const totalSyn = cy.reduce((a, b) => a + b, 0) || 1;
    this.metric("field.category_adherence", {
      column,
      value: inVocabulary / totalSyn,
      ...wilsonCi(inVocabulary, totalSyn),
    });
    const tvdValue = tvd(cs, cy);
    this.result.columnDistances.push(tvdValue ?? 0);
    this.metric("column.tvd", {
      column,
      value: tvdValue,
      baseline: tvd(cs, cr),
      noiseFloor: tvdNullExpectation(ps, nSource, nSynthetic),
    });
    this.metric("column.jsd", {
      column,
      value: jsdBits(cs, cy),
      baseline: jsdBits(cs, cr),
      noiseFloor: jsdNullExpectationBits(categories.length, nSource, nSynthetic),
    });
    const w = cohensW(cs, cy);
    this.metric("column.cohens_w", {
      column,
      value: w.value,
      baseline: cohensW(cs, cr).value,
      detail: { q_mass_on_p0: w.qMassOnP0 },
    });
    const top = cs.indexOf(Math.max(...cs));
    const topSrc = cs[top]! / nSource;
    const topSyn = cy[top]! / totalSyn;
    this.metric("column.top1_share_delta", {
      column,
      value: Math.abs(topSyn - topSrc),
      sourceValue: topSrc,
      syntheticValue: topSyn,
      baseline: Math.abs(cr[top]! / nReference - topSrc),
      ...foldedNewcombeCi(cy[top]!, totalSyn, cs[top]!, nSource),
    });
    const coverage = (counts: number[]) =>
      categories.reduce((acc, c, i) => acc + ((counts[i] ?? 0) > 0 && sourceSet.has(c) ? ps[i]! : 0), 0) /
      (ps.reduce((a, b) => a + b, 0) || 1);
    this.metric("column.coverage_mass", { column, value: coverage(cy), baseline: coverage(cr) });
    if (column.kind === "categorical") {
      this.metric("column.novelty_mass", {
        column,
        value: 1 - inVocabulary / totalSyn,
        sourceValue: 0,
      });
      this.metric("column.distinct_ratio", {
        column,
        value: cyM.filter((c) => c > 0).length / Math.max(csM.filter((c) => c > 0).length, 1),
        baseline:
          this.countsAt(ps, Math.min(m, nReference)).filter((c) => c > 0).length /
          Math.max(csM.filter((c) => c > 0).length, 1),
        detail: { matched_n: m },
        nSource: m,
        nSynthetic: m,
      });
      this.lengthAndClasses(column, categories, cs, cy, cr);
    }
    const hs = entropyBits(csM);
    const hy = entropyBits(cyM);
    this.metric("column.entropy_ratio", {
      column,
      value: hs && hy !== null ? hy / hs : null,
      sourceValue: hs,
      syntheticValue: hy,
      baseline: hs ? (entropyBits(this.countsAt(ps, Math.min(m, nReference))) ?? 0) / hs : null,
      detail: { matched_n: m },
      nSource: m,
      nSynthetic: m,
    });
    // Categorical value copies are domain density, not memorization (INFO in the evaluator): not emitted.
  }

  lengthAndClasses(column: ColumnDef, values: readonly string[], cs: number[], cy: number[], cr: number[] | null) {
    const maxLen = Math.max(...values.map((v) => v.length), 1);
    const lengthCounts = (counts: number[]) => {
      const out = new Array<number>(maxLen + 1).fill(0);
      values.forEach((v, i) => (out[v.length]! += counts[i] ?? 0));
      return out;
    };
    const ls = lengthCounts(cs);
    const ly = lengthCounts(cy);
    const lengths = ls.map((_, i) => i);
    const trim = (counts: number[]) => ({
      lengths: lengths.filter((_, i) => ls[i]! + ly[i]! > 0),
      counts: counts.filter((_, i) => ls[i]! + ly[i]! > 0),
    });
    this.profile("length_hist", "source", { ...trim(ls), overflow: 0 }, { column: column.name, n: sum(cs) });
    this.profile("length_hist", "synthetic", { ...trim(ly), overflow: 0 }, { column: column.name, n: sum(cy) });
    const bracket = ksBracket(ls, ly);
    this.metric("column.length_ks", {
      column,
      value: bracket?.dLo ?? null,
      baseline: cr ? (ksBracket(ls, lengthCounts(cr))?.dLo ?? null) : null,
      noiseFloor: ksCritical(sum(cs), sum(cy)),
      detail: bracket ? { d_lo: bracket.dLo, d_hi: bracket.dHi } : null,
      nSource: sum(cs),
      nSynthetic: sum(cy),
    });
    const classes = (counts: number[]) => {
      const total = sum(counts) || 1;
      const presence = { upper: 0, lower: 0, digit: 0, space: 0, punct: 0, other: 0 };
      values.forEach((v, i) => {
        const c = counts[i] ?? 0;
        if (/[A-Z]/.test(v)) presence.upper += c;
        if (/[a-z]/.test(v)) presence.lower += c;
        if (/[0-9]/.test(v)) presence.digit += c;
        if (/\s/.test(v)) presence.space += c;
        if (/[!-/:-@[-`{-~]/.test(v)) presence.punct += c;
        if (/[^A-Za-z0-9\s!-/:-@[-`{-~]/.test(v)) presence.other += c;
      });
      return Object.fromEntries(Object.entries(presence).map(([k, c]) => [k, c / total])) as Record<string, number>;
    };
    const ks = classes(cs);
    const ky = classes(cy);
    this.profile("char_classes", "source", { classes: ks, n: sum(cs) }, { column: column.name, n: sum(cs) });
    this.profile("char_classes", "synthetic", { classes: ky, n: sum(cy) }, { column: column.name, n: sum(cy) });
    const l1 = (a: Record<string, number>, b: Record<string, number>) =>
      Object.keys(a).reduce((acc, k) => acc + Math.abs(a[k]! - b[k]!), 0);
    this.metric("column.char_class_l1", {
      column,
      value: l1(ks, ky),
      baseline: cr ? l1(ks, classes(cr)) : null,
      nSource: sum(cs),
      nSynthetic: sum(cy),
    });
  }

  /** Free text and identifiers: diversity at matched n, copies, lifts, shapes. */
  text(column: ColumnDef) {
    const { nSource, nSynthetic, nReference, rng, spec, table } = this.ctx;
    const pool = this.ctx.pools.get(column.name);
    const strings = (rows: readonly Row[]) =>
      memo(rows, `strings:${column.name}`, () =>
        this.values(rows, column.name).filter((v): v is string => typeof v === "string" && v !== ""),
      );
    const srcValues = strings(this.ctx.source);
    const synValues = strings(this.ctx.synthetic);
    const refSet = memo(this.ctx.reference, `set:${column.name}`, () => new Set(strings(this.ctx.reference)));
    const holdSet = memo(this.ctx.holdout, `set:${column.name}`, () => new Set(strings(this.ctx.holdout)));
    const universe = textUniverse(table.name, column.name);
    const m = Math.min(nSource, nSynthetic);

    // The value distributions: the universe (finite text) or unique-ish values (identifiers).
    const synCounts = new Map<string, number>();
    for (const v of this.ctx.textDraws.get(column.name) ?? synValues) synCounts.set(v, (synCounts.get(v) ?? 0) + 1);
    const copiesR = synValues.filter((v) => refSet.has(v) && !holdSet.has(v)).length / Math.max(synValues.length, 1);
    const copiesH = synValues.filter((v) => holdSet.has(v) && !refSet.has(v)).length / Math.max(synValues.length, 1);
    let distinctSrc: number;
    let distinctSyn: number;
    let hSrc: number | null;
    let hSyn: number | null;
    let novelty: number;
    let sourceNovelty: number;
    let rareCopy: number;
    const universeSet = universe ? new Set(universe.values) : null;
    if (universe) {
      const probs = normalize(universe.weights);
      const synProbs = normalize([...synCounts.values()]);
      distinctSrc = expectedDistinct(probs, m);
      const poolCapped = pool && spec.quality.poolCollapse;
      distinctSyn = poolCapped
        ? Math.min(pool.values.length, expectedDistinct(synProbs, m))
        : expectedDistinct(synProbs, m);
      hSrc = entropyBits(probs);
      hSyn = entropyBits(synProbs);
      novelty = synValues.filter((v) => !universeSet!.has(v)).length / Math.max(synValues.length, 1);
      sourceNovelty = probs.reduce((acc, p) => acc + p * (1 - p) ** nReference, 0);
      // No value of a Zipf universe this large is rare (count < 10) in the source.
      rareCopy = universe.weights.some((w) => w * nSource < K_ANON) ? copiesR : 0;
    } else {
      const uniqueShare = new Set(srcValues).size / Math.max(srcValues.length, 1);
      distinctSrc = m * uniqueShare;
      const leakDistinct = (rate: number) => nReference * -Math.expm1((-m * rate) / Math.max(nReference, 1));
      const pooled = pool && spec.quality.poolCollapse;
      distinctSyn = pooled
        ? Math.min(m, pool.values.length + leakDistinct(copiesR + copiesH))
        : m * (1 - copiesR - copiesH) + leakDistinct(copiesR + copiesH);
      hSrc = Math.log2(Math.max(distinctSrc, 1));
      const groups = pooled
        ? [
            [1 - copiesR - copiesH, pool.values.length],
            [copiesR + copiesH, Math.max(leakDistinct(copiesR + copiesH), 1)],
          ]
        : [
            [1 - copiesR - copiesH, m * (1 - copiesR - copiesH)],
            [copiesR + copiesH, Math.max(leakDistinct(copiesR + copiesH), 1)],
          ];
      hSyn = groups.reduce((acc, [mass, k]) => (mass! > 0 ? acc - mass! * Math.log2(mass! / k!) : acc), 0);
      const sourceSet = new Set(srcValues);
      novelty = synValues.filter((v) => !sourceSet.has(v) && !refSet.has(v)).length / Math.max(synValues.length, 1);
      sourceNovelty = [...holdSet].filter((v) => !refSet.has(v)).length / Math.max(holdSet.size, 1);
      rareCopy = copiesR + copiesH;
    }
    const kCopy = rng.binomial(nSynthetic, Math.min(1, rareCopy));
    this.metric("field.substantive_copy_rate", {
      column,
      value: kCopy / nSynthetic,
      ...wilsonCi(kCopy, nSynthetic),
      method: "exact",
    });
    this.privacyLift("field.value_memorization_lift", column, copiesR, copiesH, universe !== null);
    if (column.kind === "text" && pool) {
      const poolSet = new Set(pool.values);
      const poolCopiesR =
        pool.values.filter((v) => refSet.has(v) && !holdSet.has(v)).length / Math.max(pool.values.length, 1);
      const poolCopiesH =
        pool.values.filter((v) => holdSet.has(v) && !refSet.has(v)).length / Math.max(pool.values.length, 1);
      this.privacyLift(
        "field.pool_memorization_lift",
        column,
        poolCopiesR,
        poolCopiesH,
        universe !== null,
        poolSet.size,
      );
    }
    const shape = (v: string) => v.replace(/[A-Z]/g, "A").replace(/[a-z]/g, "a").replace(/[0-9]/g, "9");
    const masks = (values: string[]) => {
      const out = new Map<string, number>();
      for (const v of values) {
        const m = shape(v);
        out.set(m, (out.get(m) ?? 0) + 1);
      }
      return out;
    };
    const ms = memo(srcValues, "masks", () => masks(srcValues));
    const my = masks(synValues);
    const total = (x: Map<string, number>) => [...x.values()].reduce((a, b) => a + b, 0) || 1;
    const sourceTotal = total(ms);
    const head = memo(srcValues, "head", () =>
      [...ms.entries()].filter(([, c]) => c / sourceTotal >= 0.02).map(([k]) => k),
    );
    const mixPayload = (x: Map<string, number>, n: number) => {
      const t = total(x);
      const items = [...x.entries()]
        .filter(([k]) => head.includes(k))
        .sort((a, b) => b[1] - a[1])
        .map(([mask, c]) => ({ mask, count: Math.round((c / t) * n), share: c / t }));
      return { items, tail_share: 1 - items.reduce((a, b) => a + b.share, 0), head_floor: 0.02 };
    };
    this.profile("shape_mix", "source", mixPayload(ms, nSource), { column: column.name, n: nSource });
    this.profile("shape_mix", "synthetic", mixPayload(my, nSynthetic), { column: column.name, n: nSynthetic });
    const shareIn = [...my.entries()].reduce((acc, [k, c]) => acc + (ms.has(k) ? c : 0), 0) / total(my);
    const kShape = rng.binomial(nSynthetic, shareIn);
    this.metric("field.shape_adherence", {
      column,
      value: kShape / nSynthetic,
      baseline: 1,
      ...wilsonCi(kShape, nSynthetic),
    });
    const headShares = (x: Map<string, number>) =>
      [...head, "__tail__"].map((k) =>
        k === "__tail__" ? total(x) - head.reduce((a, h) => a + (x.get(h) ?? 0), 0) : (x.get(k) ?? 0),
      );
    this.metric("column.shape_head_tv", {
      column,
      value: tvd(headShares(ms), headShares(my)),
      baseline: 0,
      noiseFloor: tvdNullExpectation(
        headShares(ms),
        Math.min(nSource, srcValues.length),
        Math.min(nSynthetic, synValues.length),
      ),
      nSource: srcValues.length,
      nSynthetic: synValues.length,
      method: "exact",
    });
    this.metric("column.novelty_mass", { column, value: novelty, sourceValue: sourceNovelty });
    this.metric("column.entropy_ratio", {
      column,
      value: hSrc ? (hSyn ?? 0) / hSrc : null,
      sourceValue: hSrc,
      syntheticValue: hSyn,
      baseline: 1,
      detail: { matched_n: m },
      nSource: m,
      nSynthetic: m,
    });
    const ratio = distinctSrc > 0 ? distinctSyn / distinctSrc : null;
    this.result.columnDistances.push(Math.min(1, Math.abs(1 - (ratio ?? 1)) * 0.5));
    this.metric("column.distinct_ratio", {
      column,
      value: ratio,
      sourceValue: distinctSrc,
      syntheticValue: distinctSyn,
      baseline: 1,
      detail: { matched_n: m },
      nSource: m,
      nSynthetic: m,
    });
    if (column.kind === "text") {
      const capped = !!pool && spec.quality.poolCollapse && pool.values.length >= FREE_TEXT_POOL_MAX;
      this.metric("column.distinct_ceiling_hit", {
        column,
        value: capped ? 1 : 0,
        syntheticValue: distinctSyn,
        detail: { pool_cap: FREE_TEXT_POOL_MAX, pool_size: pool?.values.length ?? null },
      });
    }
    // Lengths and character classes, on the values seen (topk labels hashed: > 50 distinct).
    const all = [...new Set([...srcValues, ...synValues])];
    const index = new Map(all.map((v, i) => [v, i]));
    const counts = (values: string[]) => {
      const out = new Array<number>(all.length).fill(0);
      for (const v of values) out[index.get(v)!]! += 1;
      return out;
    };
    const cs = counts(srcValues);
    const cy = counts(synValues);
    this.lengthAndClasses(
      column,
      all,
      cs,
      cy,
      counts(
        this.values(this.ctx.reference, column.name).filter((v): v is string => typeof v === "string" && v !== ""),
      ),
    );
    const topk = (c: number[], n: number, label: string) => {
      const order = c
        .map((x, i) => [x, i] as const)
        .sort((a, b) => b[0] - a[0])
        .slice(0, 10);
      const t = sum(c) || 1;
      const items = order.map(([x, i]) => ({
        label: `h:${sha256Hex(`${column.name}\u001f${all[i]!}`).slice(0, 8)}`,
        literal: false,
        count: Math.round((x / t) * n),
        share: x / t,
      }));
      return {
        items,
        other_count: n - items.reduce((a, b) => a + b.count, 0),
        distinct: Math.round(label === "source" ? distinctSrc : distinctSyn),
        total: n,
        nulls: 0,
      };
    };
    this.profile("topk", "source", topk(cs, nSource, "source"), { column: column.name, n: nSource, truncated: true });
    this.profile("topk", "synthetic", topk(cy, nSynthetic, "synthetic"), {
      column: column.name,
      n: nSynthetic,
      truncated: true,
    });
  }

  /** A rate-ratio lift (R vs H) on counts over the whole synthetic side. */
  privacyLift(
    id: LiftId,
    column: ColumnDef | undefined,
    rateR: number,
    rateH: number,
    commonValues: boolean,
    over?: number,
  ) {
    const { nSynthetic, nReference, rng, referenceVerified } = this.ctx;
    if (!referenceVerified)
      return this.notEvaluated(id, "reference not verified: R and H are not the generator's sample", { column });
    const n = over ?? nSynthetic;
    const m1 = rng.poisson(n * (rateR + CHANCE_MATCH));
    const m2 = rng.poisson(n * (rateH + CHANCE_MATCH));
    if (commonValues && m1 + m2 === 0)
      return this.notEvaluated(id, "no rare source values: every value is shared by ≥ 10 source rows", { column });
    return this.metric(id, { column, ...liftReading(id, m1, nReference, m2, nReference), nSynthetic: n });
  }

  // ---------------------------------------------------------------- pairs --

  pairs(numericColumns: ColumnDef[], categoricalPairs: [string, string][]) {
    const { source, synthetic } = this.ctx;
    const asNumber = (column: ColumnDef, v: Value) =>
      v === null || typeof v === "boolean" ? Number.NaN : column.kind === "temporal" ? epoch(v) : Number(v);
    const numbers = (rows: readonly Row[], column: ColumnDef) =>
      memo(rows, `asnum:${column.name}`, () => rows.map((r) => asNumber(column, r[column.name] ?? null)));
    const matrix = (rows: readonly Row[]) =>
      numericColumns.map((a) => numericColumns.map((b) => (a === b ? 1 : pearson(numbers(rows, a), numbers(rows, b)))));
    if (numericColumns.length >= 2) {
      const ms = memo(source, `corr:${numericColumns.map((c) => c.name).join(",")}`, () => matrix(source));
      const my = matrix(synthetic);
      const names = numericColumns.map((c) => c.name);
      this.profile(
        "corr_matrix",
        "source",
        { columns: names, method: "pearson", values: ms, n: source.length },
        { n: source.length },
      );
      this.profile(
        "corr_matrix",
        "synthetic",
        { columns: names, method: "pearson", values: my, n: synthetic.length },
        { n: synthetic.length },
      );
      const deltas: number[] = [];
      for (let i = 0; i < numericColumns.length; i += 1)
        for (let j = i + 1; j < numericColumns.length; j += 1) {
          const a = numericColumns[i]!;
          const b = numericColumns[j]!;
          const rs = ms[i]![j];
          const ry = my[i]![j];
          const floor = fisherZDeltaFloor(source.length, synthetic.length);
          const delta = rs === null || rs === undefined || ry === null || ry === undefined ? null : Math.abs(rs - ry);
          if (delta !== null) deltas.push(delta);
          this.metric("pair.pearson_delta", {
            column: a,
            column2: b.name,
            value: delta,
            sourceValue: rs ?? null,
            syntheticValue: ry ?? null,
            baseline: 0,
            noiseFloor: floor,
            nSource: source.length,
            nSynthetic: synthetic.length,
            method: "sample",
          });
          const complete = (rows: readonly Row[]) => {
            const xs = numbers(rows, a);
            const ys = numbers(rows, b);
            const keep = xs.map((v, k) => Number.isFinite(v) && Number.isFinite(ys[k]!));
            return [xs.filter((_, k) => keep[k]), ys.filter((_, k) => keep[k])] as const;
          };
          const [sx, sy] = memo(source, `complete:${a.name}|${b.name}`, () => complete(source));
          const [yx, yy] = complete(synthetic);
          const s1 = memo(source, `spearman:${a.name}|${b.name}`, () => spearman(sx, sy));
          const s2 = spearman(yx, yy);
          this.metric("pair.spearman_delta", {
            column: a,
            column2: b.name,
            value: s1 === null || s2 === null ? null : Math.abs(s1 - s2),
            sourceValue: s1,
            syntheticValue: s2,
            baseline: 0,
            noiseFloor: floor,
            nSource: sx.length,
            nSynthetic: yx.length,
            method: "sample",
          });
        }
      const rms = deltas.length ? Math.sqrt(deltas.reduce((a, b) => a + b * b, 0) / deltas.length) : null;
      this.metric("table.corr_rms_delta", {
        value: rms,
        baseline: 0,
        nSource: source.length,
        nSynthetic: synthetic.length,
        method: "sample",
      });
      this.metric("table.corr_max_delta", {
        value: deltas.length ? Math.max(...deltas) : null,
        baseline: 0,
        nSource: source.length,
        nSynthetic: synthetic.length,
        method: "sample",
      });
    }
    for (const [a, b] of categoricalPairs) {
      const colA = this.ctx.table.columns.find((c) => c.name === a)!;
      const colB = this.ctx.table.columns.find((c) => c.name === b)!;
      const bucket = (column: ColumnDef, v: Value): string | null => {
        if (v === null) return null;
        if (column.kind === "numeric") {
          const n = Number(v);
          return n < 20 ? "<20" : n < 50 ? "20-50" : n < 100 ? "50-100" : "≥100";
        }
        return String(v);
      };
      const labels = (column: ColumnDef) =>
        [
          ...new Set(
            this.ctx.source.map((r) => bucket(column, r[column.name] ?? null)).filter((v): v is string => v !== null),
          ),
        ].sort();
      const xs = labels(colA);
      const ys = labels(colB);
      const table = (rows: readonly Row[]) => {
        const t = xs.map(() => ys.map(() => 0));
        for (const r of rows) {
          const i = xs.indexOf(bucket(colA, r[a] ?? null) ?? "");
          const j = ys.indexOf(bucket(colB, r[b] ?? null) ?? "");
          if (i >= 0 && j >= 0) t[i]![j]! += 1;
        }
        return t;
      };
      const ts = memo(this.ctx.source, `contingency:${a}|${b}`, () => table(this.ctx.source));
      const ty = table(this.ctx.synthetic);
      const n = sum(ts.flat());
      for (const [side, t] of [
        ["source", ts],
        ["synthetic", ty],
      ] as const)
        this.profile(
          "contingency",
          side,
          { column_x: a, column_y: b, x_labels: xs, y_labels: ys, counts: t },
          { n: sum(t.flat()) },
        );
      const vs = cramersV(ts);
      const vy = cramersV(ty);
      this.metric("pair.cramers_v_delta", {
        column: colA,
        column2: b,
        value: vs === null || vy === null ? null : Math.abs(vs - vy),
        detail: undefinedOn("Cramér's V", vs, vy),
        sourceValue: vs,
        syntheticValue: vy,
        baseline: 0,
        nSource: n,
        nSynthetic: sum(ty.flat()),
        method: "sample",
      });
      const ns = nmi(ts);
      const ny = nmi(ty);
      this.metric("pair.nmi_delta", {
        column: colA,
        column2: b,
        value: ns === null || ny === null ? null : Math.abs(ns - ny),
        detail: undefinedOn("NMI", ns, ny),
        sourceValue: ns,
        syntheticValue: ny,
        baseline: 0,
        noiseFloor: miBiasNats(xs.length, ys.length, Math.min(n, sum(ty.flat()))) / Math.LN2,
        nSource: n,
        nSynthetic: sum(ty.flat()),
        method: "sample",
      });
      this.metric("pair.contingency_tvd", {
        column: colA,
        column2: b,
        value: contingencyTvd(ts, ty),
        baseline: 0,
        // The null TVD expectation over the joint cells (the catalogue's tvd_null).
        noiseFloor: tvdNullExpectation(ts.flat(), n, sum(ty.flat())),
        nSource: n,
        nSynthetic: sum(ty.flat()),
        method: "sample",
      });
    }
  }

  // ------------------------------------------------------------------ rows --

  nullPatterns() {
    const columns = this.ctx.table.columns.filter((c) => !c.role).map((c) => c.name);
    if (columns.length > 64) {
      // The profiler and the evaluator cap null-pattern bitstrings at 64 columns.
      this.notEvaluated("row.null_pattern_tvd", `null patterns are not profiled above 64 columns (${columns.length})`);
      return;
    }
    const patterns = (rows: readonly Row[]) => {
      const out = new Map<string, number>();
      for (const r of rows) {
        const bits = columns.map((c) => (r[c] === null || r[c] === undefined ? "1" : "0")).join("");
        out.set(bits, (out.get(bits) ?? 0) + 1);
      }
      return out;
    };
    const ps = patterns(this.ctx.source);
    const py = patterns(this.ctx.synthetic);
    const keys = [...new Set([...ps.keys(), ...py.keys()])];
    const vs = keys.map((k) => ps.get(k) ?? 0);
    const vy = keys.map((k) => py.get(k) ?? 0);
    const cs = this.countsAt(vs, this.ctx.nSource);
    const cy = this.countsAt(vy, this.ctx.nSynthetic);
    for (const [side, counts, n] of [
      ["source", cs, this.ctx.nSource],
      ["synthetic", cy, this.ctx.nSynthetic],
    ] as const) {
      const order = counts
        .map((c, i) => [c, i] as const)
        .sort((a, b) => b[0] - a[0])
        .slice(0, 8);
      this.profile(
        "null_patterns",
        side,
        {
          columns,
          patterns: order.map(([c, i]) => ({ bits: keys[i]!, count: c, share: c / n })),
          overflow: n - order.reduce((a, [c]) => a + c, 0),
        },
        { n },
      );
    }
    this.metric("row.null_pattern_tvd", {
      value: tvd(cs, cy),
      baseline: tvd(cs, this.countsAt(vs, this.ctx.nReference)),
      noiseFloor: tvdNullExpectation(vs, this.ctx.nSource, this.ctx.nSynthetic),
    });
  }

  /** Rows encoded once for Gower: numbers (temporal as epoch seconds) scaled by the source range, the rest as strings. */
  private encode(
    rows: readonly Row[],
    columns: ColumnDef[],
    ranges: Map<string, number>,
  ): (number | string | null)[][] {
    return rows.map((row) =>
      columns.map((column) => {
        const v = row[column.name] ?? null;
        if (v === null) return null;
        if (column.kind === "numeric" || column.kind === "temporal") {
          const n = column.kind === "temporal" ? epoch(v as string | number) : Number(v);
          return Number.isFinite(n) ? n / (ranges.get(column.name) || 1) : String(v);
        }
        return String(v);
      }),
    );
  }

  /** Gower distance: mean over columns of |Δ|/range (numbers, capped at 1) or 0/1 (anything else); null vs null = 0. */
  private gower(a: readonly (number | string | null)[], b: readonly (number | string | null)[]): number {
    let total = 0;
    for (let i = 0; i < a.length; i += 1) {
      const x = a[i]!;
      const y = b[i]!;
      if (x === null || y === null) total += x === y ? 0 : 1;
      else if (typeof x === "number" && typeof y === "number") total += Math.min(1, Math.abs(x - y));
      else total += x === y ? 0 : 1;
    }
    return total / a.length;
  }

  rows() {
    const { spec, nSynthetic, nReference, rng, referenceVerified } = this.ctx;
    const q = spec.quality;
    const nPrivacy = Math.min(50_000, nSynthetic);
    const columns = this.ctx.table.columns.filter((c) => !c.role);
    const ranges = memo(this.ctx.source, "ranges", () => {
      const out = new Map<string, number>();
      for (const c of columns) {
        if (c.kind !== "numeric" && c.kind !== "temporal") continue;
        const vs = this.ctx.source
          .map((r) => r[c.name] ?? null)
          .filter((v) => v !== null)
          .map((v) => (c.kind === "temporal" ? epoch(v as string | number) : Number(v)));
        out.set(c.name, Math.max(...vs) - Math.min(...vs));
      }
      return out;
    });
    // Exact copies and near copies, counted over the privacy sample (m1: R, m2: H).
    const exactNonKey = rng.binomial(nPrivacy, q.rowLeak + q.exposureLeak + CHANCE_MATCH * 10);
    const exact = rng.binomial(nPrivacy, CHANCE_MATCH);
    this.metric("row.exact_match_rate", {
      value: exact / nPrivacy,
      ...wilsonCi(exact, nPrivacy),
      nSynthetic: nPrivacy,
    });
    this.metric("row.exact_match_rate_nonkey", {
      value: exactNonKey / nPrivacy,
      ...wilsonCi(exactNonKey, nPrivacy),
      nSynthetic: nPrivacy,
    });
    const lift = (id: LiftId, rateR: number, sizeR: number) => {
      if (!referenceVerified)
        return this.notEvaluated(id, "reference not verified: R and H are not the generator's sample");
      const m1 = rng.poisson(nPrivacy * (rateR + CHANCE_MATCH * 10));
      const m2 = rng.poisson(nPrivacy * CHANCE_MATCH * 10);
      return this.metric(id, { ...liftReading(id, m1, sizeR, m2, sizeR), nSynthetic: nPrivacy });
    };
    lift("row.memorization_lift", q.rowLeak, nReference);
    lift("row.exposure_lift", q.exposureLeak * (nReference / 1024), 1024);
    const near = rng.binomial(nPrivacy, q.nearLeak + CHANCE_MATCH * 20);
    this.metric("row.near_match_rate", {
      value: near / nPrivacy,
      ...wilsonCi(near, nPrivacy),
      nSynthetic: nPrivacy,
    });
    lift("row.near_match_lift", q.nearLeak, nReference);
    /** Rows whose non-key content another row already holds. */
    const duplicates = (rows: readonly Row[]) =>
      rows.length - new Set(rows.map((r) => columns.map((c) => String(r[c.name])).join("\u001f"))).size;
    const dupShare = (rows: readonly Row[]) => duplicates(rows) / Math.max(rows.length, 1);
    const sourceDup = memo(this.ctx.source, "dup", () => dupShare(this.ctx.source));
    // A signed difference of two shares: Newcombe's interval, unfolded (the catalogue's newcombe).
    const [dupLow, dupHigh] = newcombe(
      duplicates(this.ctx.synthetic),
      this.ctx.synthetic.length,
      memo(this.ctx.source, "dupRows", () => duplicates(this.ctx.source)),
      this.ctx.source.length,
    );
    this.metric("row.internal_duplicate_excess", {
      value: dupShare(this.ctx.synthetic) - sourceDup,
      ciLow: dupLow,
      ciHigh: dupHigh,
      baseline: memo(this.ctx.reference, "dup", () => dupShare(this.ctx.reference)) - sourceDup,
      nSource: this.ctx.source.length,
      nSynthetic: this.ctx.synthetic.length,
      method: "sample",
    });
    this.nullPatterns();

    // Nearest-record distances on a small panel: syn→R, syn→H, H→R (Gower).
    const P = 100;
    const R = memo(this.ctx.reference, "panel", () => this.encode(this.ctx.reference.slice(0, P), columns, ranges));
    const H = memo(this.ctx.holdout, "panel", () => this.encode(this.ctx.holdout.slice(0, P), columns, ranges));
    const synRows = this.ctx.synthetic.slice(0, P);
    const S = this.encode(synRows, columns, ranges);
    type Encoded = (number | string | null)[];
    const nearest = (row: Encoded, pool: readonly Encoded[]) => {
      let d1 = Infinity;
      let d2 = Infinity;
      for (const other of pool) {
        const d = this.gower(row, other);
        if (d < d1) [d1, d2] = [d, d1];
        else if (d < d2) d2 = d;
      }
      return [d1, d2] as const;
    };
    const synR = S.map((r) => nearest(r, R));
    const synH = S.map((r) => nearest(r, H));
    const holdR = memo(H, "nearestR", () => H.map((r) => nearest(r, R)));
    if (!referenceVerified) {
      for (const id of ["row.dcr_train_holdout_share", "row.dcr_p5_ratio", "row.nndr_p5_ratio"] as const)
        this.notEvaluated(id, "reference not verified: R and H are not the generator's sample");
    } else {
      // Share of synthetic rows closer to R than to H (ties count ½), with its Wilson interval at the panel size.
      const closer = synR.reduce((acc, [d], i) => acc + (d < synH[i]![0] ? 1 : d === synH[i]![0] ? 0.5 : 0), 0);
      const [lo, hi] = wilson(closer, S.length);
      this.metric("row.dcr_train_holdout_share", {
        value: closer / S.length,
        ciLow: lo,
        ciHigh: hi,
        nSource: R.length,
        nSynthetic: S.length,
        method: "sample",
      });
      const p5 = (values: number[]) => quantiles(values, [0.05])[0]!;
      const dSyn = synR.map(([d]) => d);
      const dHold = holdR.map(([d]) => d);
      this.metric("row.dcr_p5_ratio", {
        value: p5(dHold) > 0 ? p5(dSyn) / p5(dHold) : null,
        sourceValue: p5(dHold),
        syntheticValue: p5(dSyn),
        nSource: H.length,
        nSynthetic: S.length,
        method: "sample",
      });
      const nndr = (pairs: (readonly [number, number])[]) => pairs.map(([d1, d2]) => (d2 > 0 ? d1 / d2 : 1));
      const nnSyn = nndr(synR);
      const nnHold = nndr(holdR);
      this.metric("row.nndr_p5_ratio", {
        value: p5(nnHold) > 0 ? p5(nnSyn) / p5(nnHold) : null,
        sourceValue: p5(nnHold),
        syntheticValue: p5(nnSyn),
        nSource: H.length,
        nSynthetic: S.length,
        method: "sample",
      });
      const edges = Array.from({ length: 9 }, (_, i) => (i + 1) / 10);
      this.profile(
        "dcr_hist",
        "synthetic",
        { edges, counts: histogram(dSyn, edges), p5: p5(dSyn), p50: quantiles(dSyn, [0.5])[0]!, n: S.length },
        { n: S.length, edges },
      );
      this.profile(
        "dcr_hist",
        "holdout",
        { edges, counts: histogram(dHold, edges), p5: p5(dHold), p50: quantiles(dHold, [0.5])[0]!, n: H.length },
        { n: H.length, edges },
      );
      this.profile(
        "nndr_hist",
        "synthetic",
        { edges, counts: histogram(nnSyn, edges), p5: p5(nnSyn), p50: quantiles(nnSyn, [0.5])[0]!, n: S.length },
        { n: S.length, edges },
      );
      this.profile(
        "nndr_hist",
        "holdout",
        { edges, counts: histogram(nnHold, edges), p5: p5(nnHold), p50: quantiles(nnHold, [0.5])[0]!, n: H.length },
        { n: H.length, edges },
      );
      this.flagRows(synR, synRows);
    }
    // PRDC density and coverage (Naeem et al. 2020) with k = 5 on the same panel.
    const k = 5;
    const radius = memo(R, "radius", () =>
      R.map((r) => {
        const ds = R.filter((o) => o !== r)
          .map((o) => this.gower(r, o))
          .sort((a, b) => a - b);
        return ds[k - 1] ?? 0;
      }),
    );
    let density = 0;
    for (const s of S) R.forEach((r, i) => (density += this.gower(s, r) <= radius[i]! ? 1 : 0));
    density /= k * S.length;
    const coverage = R.filter((r, i) => S.some((s) => this.gower(s, r) <= radius[i]!)).length / R.length;
    this.metric("row.density", {
      value: density,
      baseline: 1,
      nSource: R.length,
      nSynthetic: S.length,
      method: "sample",
    });
    this.metric("row.coverage", {
      value: coverage,
      baseline: 1,
      nSource: R.length,
      nSynthetic: S.length,
      method: "sample",
    });
  }

  flagRows(synR: (readonly [number, number])[], S: readonly Row[]) {
    const pk = this.ctx.table.columns.find((c) => c.role === "pk")?.name;
    const order = synR.map(([d], i) => [d, i] as const).sort((a, b) => a[0] - b[0]);
    const exact = order.filter(([d]) => d === 0).slice(0, 12);
    const near = order.filter(([d]) => d > 0 && d < 0.12).slice(0, 12);
    const push = (
      check: EvaluationRowFlagsRow["check"],
      list: (readonly [number, number])[],
      set: EvaluationRowFlagsRow["source_set"],
    ) =>
      list.forEach(([d, i], rank) =>
        this.result.flags.push({
          evaluation_id: this.ctx.spec.id,
          evaluated_at: this.ctx.evaluatedAt,
          table_name: this.ctx.table.name,
          check,
          rank: rank + 1,
          synthetic_key: pk ? { [pk]: S[i]![pk] ?? null } : null,
          // The evaluator's keyed-hash label: `h:` and eight hex digits; the key itself is never written.
          source_key_hash: `h:${sha256Hex(`key:${this.ctx.spec.id}\u001f${this.ctx.table.name}\u001f${i}`).slice(0, 8)}`,
          source_key: null,
          source_set: set,
          distance: d,
          score: 1 - d,
          detail: { panel_index: i },
        }),
      );
    push("exact_copy", exact, "R");
    push("near_copy", near, "R");
    push("nearest_record", order.slice(0, 5), "R");
  }

  // ----------------------------------------------------------------- table --

  tableLevel(detectionShift: number) {
    const { rowsSynthetic, rowsExpected, nSynthetic } = this.ctx;
    this.metric("table.row_count_ratio", {
      value: rowsSynthetic / rowsExpected,
      sourceValue: rowsExpected,
      syntheticValue: rowsSynthetic,
      nSynthetic: rowsSynthetic,
    });
    const pk = this.ctx.table.columns.find((c) => c.role === "pk");
    const dupRate =
      this.ctx.spec.uniquenessMode === "streaming" ? this.ctx.rng.poisson(nSynthetic * 2e-7) / nSynthetic : 0;
    if (pk) {
      this.metric("table.pk_duplicate_rate", { value: dupRate, nSynthetic: rowsSynthetic });
      this.metric("table.identity_duplicate_rate", { value: dupRate, nSynthetic: rowsSynthetic });
    }
    const nDetect = Math.min(50_000, nSynthetic);
    const auc = Math.min(0.99, 0.5 + detectionShift);
    const [lo, hi] = aucInterval(auc, nDetect, nDetect);
    this.metric("table.detection_auc", {
      value: auc,
      ciLow: lo,
      ciHigh: hi,
      baseline: 0.5,
      nSource: nDetect,
      nSynthetic: nDetect,
      method: "sample",
    });
    this.profile(
      "roc_curve",
      "both",
      { points: binormalRoc(auc, 21), auc, ci_low: lo, ci_high: hi, n_source: nDetect, n_synthetic: nDetect },
      { n: 2 * nDetect },
    );
    // The ratio's ceiling N / (k − 1) (pMSE ≤ c(1 − c)); below the fail threshold the design
    // could never FAIL, and the evaluator does not grade it (Rulings R33, R43).
    const features = this.ctx.table.columns.filter((c) => !c.role).length;
    this.metric("table.pmse_ratio", {
      value: 1 + 100 * (auc - 0.5) ** 2,
      baseline: 1,
      detail: { ceiling: (2 * nDetect) / Math.max(features, 1), k: features + 1 },
      nSource: nDetect,
      nSynthetic: nDetect,
      method: "sample",
    });
  }
}

export function sum(values: readonly number[]): number {
  return values.reduce((a, b) => a + b, 0);
}

/** Children-per-parent histogram at n parents from shares (the last bucket is "≥"). */
export function fanoutCounts(rng: Random, shares: readonly number[], parents: number): number[] {
  return rng.multinomial(parents, normalize(shares));
}
