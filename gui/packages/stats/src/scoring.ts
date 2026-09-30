/**
 * Catalogue-driven score, status and roll-ups: an exact port of the
 * evaluator's `sdfb_evaluation.scoring` (metrics.yaml header, Rulings R9–R12,
 * R26, R33, R37–R43, R45), pinned to it case by case by
 * `scoring.golden.test.ts` (golden/scoring.json, written by the Python). The
 * evaluator's `status` / `score` columns are authoritative; the mock uses this
 * to write the rows the evaluator would, and the EVALUATION tab re-reads the
 * rule only to explain a stored status.
 *
 * Status, in order (`assess`):
 * 1. the gated value g: the value, or for a `uses_ci_bound` metric its CI
 *    bound (ci_low for lower_better, ci_high for higher_better). Missing or
 *    NaN → not_evaluated. A lift gates on its bound even when its point value
 *    is null or infinite (R38): a clean run's lifts PASS, never "not evaluated";
 * 2. `table.pmse_ratio` without detail.ceiling, or with a ceiling below the
 *    fail threshold → not_evaluated (R33, R43);
 * 3. `relationship.orphan_rate` on a documented edge (enforced: false) →
 *    INFO; fan-out metrics stay graded (R42);
 * 4. no thresholds → INFO;
 * 5. a target metric reads x = |g − target| (the catalogue's target, or the
 *    row's source_value when that is null); x = g otherwise;
 * 6. an infinite g past the bad side → FAIL with detail.nonfinite; on the
 *    good side → not_evaluated (R43);
 * 7. warn = fail = 0 (integrity by construction): FAIL iff x > 0, no noise check;
 * 8. inclusive crossings with a 1e-9 relative tolerance (R9);
 * 9. a WARN/FAIL that sampling noise explains → PASS with
 *    detail.noise_downgraded_from, scored at the noise reference (R40). The
 *    check follows the catalogue's noise method (R41): scalar methods compare
 *    |g − reference| with the row's noise_floor; interval methods (wilson,
 *    newcombe, delong) ask whether [ci_low, ci_high] covers the reference —
 *    so an observed copy or an invented category, whose Wilson interval
 *    excludes its edge reference, is never downgraded (R45); rate_ratio and
 *    none have no check. Without the input a check needs, nothing is
 *    downgraded and detail.noise_check = "unavailable". The reference is the
 *    target, else 0 for lower_better and 1 for higher_better (R12).
 *
 * Score: the score function on the g status read; null for INFO and
 * not_evaluated; a downgraded row scores at its reference. `score: none` is
 * the value only on aggregate ids (R26).
 */

export type ScoreFn = "complement" | "linear" | "ratio_to_one" | "auc" | "none";
export type Direction = "lower_better" | "higher_better" | "target";

export interface ScoringMetric {
  id: string;
  level: string;
  family: string;
  direction: Direction;
  target: number | null;
  range: readonly [number | null, number | null];
  thresholds: { warn: number | null; fail: number | null };
  score: ScoreFn;
  uses_ci_bound: boolean;
  /** The catalogue's noise method: a method name, or "none" / null for no check. */
  noise_floor: string | null;
}

export type MetricStatus = "pass" | "warn" | "fail" | "info" | "not_evaluated";

/** A producer's numbers for one metric (the Python `MetricValue`); may hold ±Infinity or NaN. */
export interface MetricReading {
  value: number | null;
  ciLow?: number | null;
  ciHigh?: number | null;
  noiseFloor?: number | null;
  /** The source-side statistic (the target of a null-target `target` metric). */
  sourceValue?: number | null;
  /** The producer's detail (reason, pMSE ceiling …). */
  detail?: Readonly<Record<string, unknown>> | null;
}

export interface ScoringOptions {
  /** false for a documented foreign-key edge: its orphan rate is INFO (R42). Default true. */
  enforced?: boolean;
}

export type GateSource = "value" | "ci_low" | "ci_high";

export interface Assessment {
  status: MetricStatus;
  /** Keys merged into the row's detail: reason, noise_downgraded_from, noise_check, nonfinite. */
  notes: Record<string, string> | null;
  /** What the score reads: g, or the noise reference after a downgrade. */
  scoreAt: number | null;
  /** The effective target of a `target` metric. */
  target: number | null;
  gateSource: GateSource;
  /** g (null when status could not read it). */
  gated: number | null;
}

export const SCALAR_NOISE_METHODS: ReadonlySet<string> = new Set([
  "ks_two_sample",
  "tvd_null",
  "jsd_null",
  "fisher_z",
  "mi_bias",
]);
export const INTERVAL_NOISE_METHODS: ReadonlySet<string> = new Set(["wilson", "newcombe", "delong"]);

const PMSE_ID = "table.pmse_ratio";
const ORPHAN_ID = "relationship.orphan_rate";
const REL_TOL = 1e-9;

const clip01 = (x: number) => Math.min(1, Math.max(0, x));
const missing = (x: number | null | undefined): x is null | undefined => x === null || x === undefined;

/** The roll-up ids (R11, R43): `table.*_score` and every `model.*` id. */
export function isAggregateMetric(metricId: string): boolean {
  return metricId.startsWith("model.") || (metricId.startsWith("table.") && metricId.endsWith("_score"));
}

/** 1 at or under warn, 0 at or over fail, linear between (lower is better). */
function band(x: number, warn: number, fail: number): number {
  if (x <= warn) return 1;
  if (x >= fail) return 0;
  return (fail - x) / (fail - warn);
}

/**
 * `value` mapped to [0, 1] (higher is better) by the metric's score function
 * (Python `score_value`). For a `uses_ci_bound` metric pass the bound status
 * gates on. `target` is used only when the catalogue's target is null.
 */
export function scoreValue(
  metric: ScoringMetric,
  value: number | null | undefined,
  options: { target?: number | null } = {},
): number | null {
  if (missing(value) || Number.isNaN(value)) return null;
  const { warn, fail } = metric.thresholds;
  switch (metric.score) {
    case "none":
      return value >= 0 && value <= 1 && isAggregateMetric(metric.id) ? value : null;
    case "complement": {
      const top = metric.range[1];
      if (top === null || top <= 0) throw new Error(`${metric.id}: complement needs a positive range top`);
      return clip01(1 - Math.abs(value) / top);
    }
    case "auc":
      return clip01(1 - 2 * Math.max(0, value - 0.5));
  }
  if (warn === null || fail === null) return null;
  if (metric.score === "ratio_to_one" || metric.direction === "target") {
    const goal = metric.target ?? options.target ?? null;
    if (goal === null || !Number.isFinite(goal)) return null;
    return clip01(band(Math.abs(value - goal), warn, fail));
  }
  if (metric.direction === "higher_better") return clip01(band(-value, -warn, -fail));
  return clip01(band(value, warn, fail));
}

/** Whether x is at or past the threshold (inclusive, relative tolerance 1e-9 — Python `math.isclose`). */
export function reached(x: number, threshold: number | null, higherBetter: boolean): boolean {
  if (threshold === null) return false;
  // math.isclose: an infinite x is close only to itself.
  const close = Number.isFinite(x)
    ? Math.abs(x - threshold) <= REL_TOL * Math.max(Math.abs(x), Math.abs(threshold))
    : x === threshold;
  if (close) return true;
  return higherBetter ? x <= threshold : x >= threshold;
}

/** The value a noise check compares with (R12): the target, else 1 for higher_better, 0 otherwise. */
export function noiseReference(metric: Pick<ScoringMetric, "direction" | "target">, target: number | null): number {
  if (target !== null) return target;
  if (metric.target !== null) return metric.target;
  return metric.direction === "higher_better" ? 1 : 0;
}

/** Whether sampling noise explains g (R41); null when the check lacks its input, false for methods with none. */
export function isNoise(
  method: string | null,
  reading: Pick<MetricReading, "ciLow" | "ciHigh" | "noiseFloor">,
  gated: number,
  reference: number,
): boolean | null {
  if (method !== null && SCALAR_NOISE_METHODS.has(method)) {
    const floor = reading.noiseFloor;
    if (missing(floor) || Number.isNaN(floor)) return null;
    return Math.abs(gated - reference) <= floor;
  }
  if (method !== null && INTERVAL_NOISE_METHODS.has(method)) {
    const { ciLow: low, ciHigh: high } = reading;
    if (missing(low) || missing(high) || Number.isNaN(low) || Number.isNaN(high)) return null;
    return low <= reference && reference <= high;
  }
  return false;
}

/** Step 1: what status reads, the value or its CI bound (R1, R12, R38). */
function gatedOf(metric: ScoringMetric, reading: MetricReading): { source: GateSource; g: number | null; why: string } {
  if (!metric.uses_ci_bound) {
    return { source: "value", g: reading.value, why: missing(reading.value) ? "no value computed" : "value is NaN" };
  }
  if (metric.direction === "target") throw new Error(`${metric.id}: uses_ci_bound needs a one-sided direction`);
  const source: GateSource = metric.direction === "higher_better" ? "ci_high" : "ci_low";
  const bound = source === "ci_high" ? reading.ciHigh : reading.ciLow;
  if (missing(bound) || Number.isNaN(bound))
    return { source, g: null, why: `${source} missing: ${metric.id} gates on its confidence bound` };
  const pointMissing = missing(reading.value) || !Number.isFinite(reading.value);
  if (pointMissing && !Number.isFinite(bound))
    return { source, g: null, why: `neither the value nor ${source} is finite` };
  return { source, g: bound, why: "" };
}

function reasonOf(reading: MetricReading): string | null {
  const reason = reading.detail?.reason;
  // Python: str(detail.get("reason") or missing). Falsy reasons fall through to the scorer's own;
  // strings, numbers and booleans read as Python's str() for them (producers write strings). Other
  // values are JSON-encoded here where Python writes their repr — the one place the two differ.
  if (reason === undefined || reason === null || reason === "" || reason === false || reason === 0) return null;
  return typeof reason === "string" || typeof reason === "number" || typeof reason === "boolean"
    ? String(reason)
    : JSON.stringify(reason);
}

/** The full status decision (Python `_assess`), in the order the module comment lists. */
export function assess(metric: ScoringMetric, reading: MetricReading, options: ScoringOptions = {}): Assessment {
  const enforced = options.enforced ?? true;
  const { source, g, why } = gatedOf(metric, reading);
  const base = { gateSource: source, gated: g };
  const notEvaluated = (reason: string): Assessment => ({
    status: "not_evaluated",
    notes: { reason },
    scoreAt: null,
    target: null,
    ...base,
  });
  if (missing(g) || Number.isNaN(g)) return notEvaluated(reasonOf(reading) ?? why);
  if (metric.id === PMSE_ID) {
    const raw = reading.detail?.ceiling;
    const ceiling = raw === undefined || raw === null ? null : Number(raw);
    if (ceiling === null || Number.isNaN(ceiling)) return notEvaluated("pmse ceiling missing");
    if (metric.thresholds.fail !== null && ceiling < metric.thresholds.fail)
      return notEvaluated("ceiling below fail threshold");
  }
  if (metric.id === ORPHAN_ID && !enforced)
    return {
      status: "info",
      notes: { reason: "documented edge (enforced: false): reported, not gated" },
      scoreAt: null,
      target: null,
      ...base,
    };
  const { warn, fail } = metric.thresholds;
  if (warn === null && fail === null) return { status: "info", notes: null, scoreAt: null, target: null, ...base };
  let target: number | null = null;
  let x = g;
  if (metric.direction === "target") {
    target = metric.target ?? reading.sourceValue ?? null;
    if (target === null || !Number.isFinite(target))
      return notEvaluated("no target: source_value missing or non-finite");
    x = Math.abs(g - target);
  }
  if (!Number.isFinite(g)) {
    const sign = g > 0 ? "+inf" : "-inf";
    const badSide = metric.direction === "target" || g > 0 !== (metric.direction === "higher_better");
    if (!badSide) return notEvaluated(`${source} is ${sign}, on the good side`);
    return { status: "fail", notes: { nonfinite: sign }, scoreAt: g, target, ...base };
  }
  // Step 7: integrity by construction.
  if (warn === 0 && fail === 0) return { status: x > 0 ? "fail" : "pass", notes: null, scoreAt: g, target, ...base };
  const higherBetter = metric.direction === "higher_better";
  let status: MetricStatus;
  if (reached(x, fail, higherBetter)) status = "fail";
  else if (reached(x, warn, higherBetter)) status = "warn";
  else return { status: "pass", notes: null, scoreAt: g, target, ...base };
  const reference = noiseReference(metric, target);
  const noise = isNoise(metric.noise_floor, reading, g, reference);
  if (noise === null) return { status, notes: { noise_check: "unavailable" }, scoreAt: g, target, ...base };
  if (noise) return { status: "pass", notes: { noise_downgraded_from: status }, scoreAt: reference, target, ...base };
  return { status, notes: null, scoreAt: g, target, ...base };
}

/** The status the catalogue rule gives (Python `status_for`). */
export function statusFor(metric: ScoringMetric, reading: MetricReading, options: ScoringOptions = {}): MetricStatus {
  return assess(metric, reading, options).status;
}

/** A number as a JSON column holds it: non-finite → null (Python `json_safe`). */
export function jsonSafeNumber(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function jsonSafe(value: unknown): unknown {
  if (typeof value === "number") return jsonSafeNumber(value);
  if (Array.isArray(value)) return value.map(jsonSafe);
  if (value && typeof value === "object")
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, jsonSafe(v)]));
  return value;
}

/** The scoring columns of one evaluation_metrics row, as Python `to_metric_row` writes them. */
export interface ScoredRow {
  status: MetricStatus;
  score: number | null;
  /** The producer's detail plus the assessment's notes; null when empty. */
  detail: Record<string, unknown> | null;
  value: number | null;
  ci_low: number | null;
  ci_high: number | null;
  noise_floor: number | null;
  source_value: number | null;
}

export function scoreRow(metric: ScoringMetric, reading: MetricReading, options: ScoringOptions = {}): ScoredRow {
  const a = assess(metric, reading, options);
  const merged = { ...(reading.detail ?? {}), ...(a.notes ?? {}) };
  const scored = a.status !== "info" && a.status !== "not_evaluated";
  return {
    status: a.status,
    score: scored ? scoreValue(metric, a.scoreAt, { target: a.target }) : null,
    detail: Object.keys(merged).length ? (jsonSafe(merged) as Record<string, unknown>) : null,
    value: jsonSafeNumber(reading.value),
    ci_low: jsonSafeNumber(reading.ciLow),
    ci_high: jsonSafeNumber(reading.ciHigh),
    noise_floor: jsonSafeNumber(reading.noiseFloor),
    source_value: jsonSafeNumber(reading.sourceValue),
  };
}

// -------------------------------------------------------------- roll-ups --

export const FAMILIES = ["fidelity", "privacy", "integrity", "diversity"] as const;
export type Family = (typeof FAMILIES)[number];
/** The model-wide key of `aggregateScores` (Python `MODEL_KEY`). */
export const MODEL_KEY = "__model__";

/** The zero-tolerance integrity metrics (warn = fail = 0), derived from the catalogue (R39). */
export function zeroToleranceIds(metrics: Iterable<Pick<ScoringMetric, "id" | "family" | "thresholds">>): Set<string> {
  const out = new Set<string>();
  for (const m of metrics)
    if (m.family === "integrity" && m.thresholds.warn === 0 && m.thresholds.fail === 0) out.add(m.id);
  return out;
}

/** The fields a roll-up reads from an evaluation_metrics row. */
export interface RollupRow {
  metric_id: string;
  table_name: string | null;
  family: string;
  level: string;
  column_name: string | null;
  edge: string | null;
  status: string;
  score: number | null;
}

/**
 * A Neumaier-compensated sum: much less order-dependent than a plain sum, but not Python's
 * `math.fsum` (Shewchuk's exactly rounded sum) — the two can differ in the last ulp, well inside
 * the golden test's 1e-12 tolerance.
 */
function compensatedSum(xs: readonly number[]): number {
  let sum = 0;
  let c = 0;
  for (const x of xs) {
    const t = sum + x;
    c += Math.abs(sum) >= Math.abs(x) ? sum - t + x : x - t + sum;
    sum = t;
  }
  return sum + c;
}

const mean = (xs: readonly (number | null)[]): number | null => {
  const present = xs.filter((x): x is number => x !== null);
  return present.length ? compensatedSum(present) / present.length : null;
};

/** The roll-up unit of a non-aggregate row (R11): a column, the pair group, a row/table metric, an edge. */
function unitOf(row: Pick<RollupRow, "level" | "column_name" | "metric_id" | "edge">): string {
  switch (row.level) {
    case "field":
    case "column":
      return `column\u0000${row.column_name ?? ""}`;
    case "pair":
      return "pair";
    case "row":
    case "table":
      return `${row.level}\u0000${row.metric_id}`;
    case "relationship":
      return `edge\u0000${row.edge ?? ""}`;
    default:
      throw new Error(`no roll-up unit for level ${JSON.stringify(row.level)}`);
  }
}

export interface FamilyScores {
  fidelity: number | null;
  privacy: number | null;
  integrity: number | null;
  diversity: number | null;
  overall: number | null;
}

export type AggregateScores = FamilyScores & {
  /** FAILed zero-tolerance integrity rows (duplicate keys, orphans on enforced edges; R39). */
  integrity_fail: number;
};

/**
 * Family and overall scores per table and for the model (Python
 * `aggregate_scores`): a table's family score is the mean over UNITS with a
 * scored row; aggregate rows are skipped and create no table; the model's
 * family score is the mean of the tables', its overall the mean of its
 * families. Keys: table names sorted, then `MODEL_KEY`.
 */
export function aggregateScores(
  rows: Iterable<RollupRow>,
  zeroTolerance: ReadonlySet<string>,
): Record<string, AggregateScores> {
  const units = new Map<string, Map<string, Map<string, number[]>>>();
  const fails = new Map<string, number>();
  const tables = new Set<string>();
  for (const row of rows) {
    if (isAggregateMetric(row.metric_id)) continue;
    const table = row.table_name;
    if (table === null) throw new Error(`${row.metric_id} row has no table_name`);
    tables.add(table);
    if (zeroTolerance.has(row.metric_id) && row.status === "fail") fails.set(table, (fails.get(table) ?? 0) + 1);
    if (row.score === null || !Number.isFinite(row.score)) continue;
    const families = units.get(table) ?? new Map<string, Map<string, number[]>>();
    units.set(table, families);
    const byUnit = families.get(row.family) ?? new Map<string, number[]>();
    families.set(row.family, byUnit);
    const key = unitOf(row);
    byUnit.set(key, [...(byUnit.get(key) ?? []), row.score]);
  }
  const result: Record<string, AggregateScores> = {};
  // Python sorts str keys by code point; so does a plain comparison of JS strings of BMP text.
  for (const table of [...tables].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))) {
    const families = units.get(table);
    const scores = {} as AggregateScores;
    for (const family of FAMILIES)
      scores[family] = mean([...(families?.get(family)?.values() ?? [])].map((unit) => mean(unit)));
    scores.overall = mean(FAMILIES.map((f) => scores[f]));
    scores.integrity_fail = fails.get(table) ?? 0;
    result[table] = scores;
  }
  const model = {} as AggregateScores;
  for (const family of FAMILIES) model[family] = mean(Object.values(result).map((s) => s[family]));
  model.overall = mean(FAMILIES.map((f) => model[f]));
  model.integrity_fail = [...fails.values()].reduce((a, b) => a + b, 0);
  result[MODEL_KEY] = model;
  return result;
}

/** One table's family scores from its rows (the `aggregateScores` rule for a single table). */
export function tableFamilyScores(rows: readonly RollupRow[]): FamilyScores {
  const table = rows.find((r) => !isAggregateMetric(r.metric_id))?.table_name ?? "";
  const scores = aggregateScores(
    rows.filter((r) => !isAggregateMetric(r.metric_id)).map((r) => ({ ...r, table_name: table })),
    new Set(),
  )[table];
  const { fidelity = null, privacy = null, integrity = null, diversity = null, overall = null } = scores ?? {};
  return { fidelity, privacy, integrity, diversity, overall };
}

/** Model scores: the mean of the table family scores; overall = mean of the available families. */
export function modelFamilyScores(tables: readonly FamilyScores[]): FamilyScores {
  const out = {} as FamilyScores;
  for (const family of FAMILIES) out[family] = mean(tables.map((t) => t[family]));
  out.overall = mean(FAMILIES.map((f) => out[f]));
  return out;
}

export type HeadlineCounts = Record<MetricStatus | "total", number>;

/**
 * How many measured rows have each status, plus total (Python
 * `headline_counts`, R37/R43): aggregate ids are left out, every status is a
 * key, and pass + warn + fail + info + not_evaluated = total.
 */
export function headlineCounts(rows: Iterable<Pick<RollupRow, "metric_id" | "status">>): HeadlineCounts {
  const counts: HeadlineCounts = { pass: 0, warn: 0, fail: 0, info: 0, not_evaluated: 0, total: 0 };
  for (const row of rows) {
    if (isAggregateMetric(row.metric_id)) continue;
    if (!(row.status in counts) || row.status === "total") throw new Error(`unknown status ${row.status}`);
    counts[row.status as MetricStatus] += 1;
    counts.total += 1;
  }
  return counts;
}
