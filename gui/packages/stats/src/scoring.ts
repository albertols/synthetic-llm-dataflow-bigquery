/**
 * Catalogue-driven score, status and roll-ups: the GUI's mirror of the
 * evaluator's `scoring` (plan Task 14, metrics.yaml header, Rulings R9–R11).
 * The evaluator's `status` / `score` columns are authoritative; the mock uses
 * these to produce consistent rows, and views use them for what-if readings.
 *
 * Status (D5):
 * - value null → not_evaluated; no thresholds → info;
 * - `uses_ci_bound` metrics read ci_low (ci_high when higher is better);
 * - target metrics read d = |v − target| (a null target → the row's source_value);
 * - warn 0 / fail 0: FAIL iff v > 0 (integrity by construction);
 * - crossing is inclusive: lower-better WARN at ≥ warn, FAIL at ≥ fail;
 *   higher-better WARN at ≤ warn, FAIL at ≤ fail;
 * - a crossing within the noise floor of the noise reference is PASS
 *   (reference: target, else 0 for lower-better, the range top for higher-better).
 */

export interface ScoringMetric {
  id: string;
  level: string;
  family: string;
  direction: "lower_better" | "higher_better" | "target";
  target: number | null;
  range: readonly [number | null, number | null];
  thresholds: { warn: number | null; fail: number | null };
  score: "complement" | "linear" | "ratio_to_one" | "auc" | "none";
  uses_ci_bound: boolean;
}

export type MetricStatus = "pass" | "warn" | "fail" | "info" | "not_evaluated";

export interface MetricReading {
  value: number | null;
  ciLow?: number | null;
  ciHigh?: number | null;
  noiseFloor?: number | null;
  /** The source-side statistic (the target of a null-target `target` metric). */
  sourceValue?: number | null;
}

const clip01 = (x: number) => Math.min(1, Math.max(0, x));

/** The number status and score read: the value, or its CI bound for `uses_ci_bound` metrics. */
export function readValue(metric: ScoringMetric, reading: MetricReading): number | null {
  if (reading.value === null || reading.value === undefined) return null;
  if (!metric.uses_ci_bound) return reading.value;
  const bound = metric.direction === "higher_better" ? reading.ciHigh : reading.ciLow;
  return bound ?? reading.value;
}

function targetOf(metric: ScoringMetric, reading: MetricReading): number | null {
  if (metric.direction !== "target") return null;
  return metric.target ?? reading.sourceValue ?? 0;
}

/** The five score functions (Ruling R10); null when the value is missing or `none` falls outside [0, 1]. */
export function scoreValue(metric: ScoringMetric, reading: MetricReading): number | null {
  const v = readValue(metric, reading);
  if (v === null) return null;
  const { warn, fail } = metric.thresholds;
  switch (metric.score) {
    case "complement": {
      const hi = metric.range[1];
      return hi ? clip01(1 - Math.abs(v) / hi) : null;
    }
    case "linear": {
      if (warn === null || fail === null) return null;
      if (metric.direction === "higher_better") {
        if (v >= warn) return 1;
        if (v <= fail) return 0;
        return clip01((v - fail) / (warn - fail));
      }
      if (warn === fail) return v <= warn ? 1 : 0;
      if (v <= warn) return 1;
      if (v >= fail) return 0;
      return clip01((fail - v) / (fail - warn));
    }
    case "ratio_to_one": {
      if (warn === null || fail === null) return null;
      const d = Math.abs(v - (targetOf(metric, reading) ?? 1));
      if (d <= warn) return 1;
      if (d >= fail) return 0;
      return clip01((fail - d) / (fail - warn));
    }
    case "auc":
      return clip01(1 - 2 * Math.max(0, v - 0.5));
    case "none":
      return v >= 0 && v <= 1 ? v : null;
  }
}

/** The noise reference a FAIL must clear (see the module comment). */
export function noiseReference(metric: ScoringMetric, reading: MetricReading): number {
  const t = targetOf(metric, reading);
  if (t !== null) return t;
  if (metric.target !== null) return metric.target;
  return metric.direction === "higher_better" ? (metric.range[1] ?? 1) : 0;
}

export function statusFor(metric: ScoringMetric, reading: MetricReading): MetricStatus {
  const v = readValue(metric, reading);
  if (v === null) return "not_evaluated";
  const { warn, fail } = metric.thresholds;
  if (warn === null && fail === null) return "info";
  if (warn === 0 && fail === 0) return v > 0 ? "fail" : "pass";
  const floor = reading.noiseFloor;
  if (floor !== null && floor !== undefined && Math.abs(v - noiseReference(metric, reading)) <= floor) return "pass";
  if (metric.direction === "higher_better") {
    if (fail !== null && v <= fail) return "fail";
    if (warn !== null && v <= warn) return "warn";
    return "pass";
  }
  const d = metric.direction === "target" ? Math.abs(v - (targetOf(metric, reading) ?? 0)) : v;
  if (fail !== null && d >= fail) return "fail";
  if (warn !== null && d >= warn) return "warn";
  return "pass";
}

export interface ScoredRow {
  metric_id: string;
  table_name: string;
  family: string;
  level: string;
  column_name: string | null;
  edge: string | null;
  score: number | null;
}

const FAMILIES = ["fidelity", "privacy", "integrity", "diversity"] as const;
export type Family = (typeof FAMILIES)[number];

/** Aggregate ids never feed a roll-up (Ruling R11). */
export function isAggregateMetric(metricId: string): boolean {
  return (
    metricId.startsWith("model.") ||
    /^table\.(fidelity|privacy|integrity|diversity|overall)_score$/.test(metricId) ||
    metricId === "table.column_shape_score" ||
    metricId === "table.pair_trend_score"
  );
}

const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

export interface FamilyScores {
  fidelity: number | null;
  privacy: number | null;
  integrity: number | null;
  diversity: number | null;
  overall: number | null;
}

/** A table's family scores: the mean over UNITS (a column, the pair group, each row/table metric, each child edge). */
export function tableFamilyScores(rows: readonly ScoredRow[]): FamilyScores {
  const out = {} as FamilyScores;
  for (const family of FAMILIES) {
    const units = new Map<string, number[]>();
    for (const row of rows) {
      if (row.family !== family || row.score === null || isAggregateMetric(row.metric_id)) continue;
      let unit: string;
      if (row.level === "field" || row.level === "column") unit = `column:${row.column_name ?? ""}`;
      else if (row.level === "pair") unit = "pairs";
      else if (row.level === "relationship") unit = `edge:${row.edge ?? ""}`;
      else unit = `metric:${row.metric_id}`;
      const list = units.get(unit) ?? [];
      list.push(row.score);
      units.set(unit, list);
    }
    out[family] = mean([...units.values()].map((scores) => mean(scores)!));
  }
  out.overall = mean(FAMILIES.map((f) => out[f]).filter((v): v is number => v !== null));
  return out;
}

/** Model scores: the mean of the table family scores; overall = mean of the available families. */
export function modelFamilyScores(tables: readonly FamilyScores[]): FamilyScores {
  const out = {} as FamilyScores;
  for (const family of FAMILIES)
    out[family] = mean(tables.map((t) => t[family]).filter((v): v is number => v !== null));
  out.overall = mean(FAMILIES.map((f) => out[f]).filter((v): v is number => v !== null));
  return out;
}
