/**
 * Noise-aware comparison of two evaluations (the A/B diff) and the
 * comparability rules. The verdict of a pair of rows is the one `sdfb-eval
 * compare` gives it (the evaluator's `report.render`: `_delta`, `_noise`,
 * `_direction`), pinned case by case by `compare.golden.test.ts`
 * (golden/compare.json, written by the Python):
 *
 * 1. a side without a finite value: nothing to judge;
 * 2. equal values: "same", before any noise reading;
 * 3. the delta B − A is judged against what the two rows say about their own
 *    sampling noise (`noiseOf`):
 *    - both rows carry a finite noise floor → "≈" iff
 *      |Δ| ≤ √(floor_A² + floor_B²), the two estimates' noise being
 *      independent; nothing else is consulted, overlapping intervals included;
 *    - else all four CI bounds are finite → "≈" iff the intervals overlap
 *      (touching counts);
 *    - else there is no noise information, and "≈" is never read;
 * 4. otherwise better or worse by the catalogue's direction (a target metric:
 *    worse iff B is farther from the target than A), or not judged when the
 *    catalogue gives no direction.
 *
 * The view adds two things `sdfb-eval compare` reports as notes instead: a row
 * whose status is not_evaluated has no verdict, and two runs whose catalogue or
 * evaluator versions differ, or (per table) whose encoding_plan_digest differs,
 * are not comparable — the delta is still shown, flagged, and its direction is
 * never judged ("≈" and "same" still are).
 */
import type { ComparedMetric, Comparison, EvaluationSummary, MetricCell } from "@contracts/api";

import { metricMeta } from "./catalogue";

export type DiffVerdict = "approx" | "better" | "worse" | "same" | "missing" | "not_evaluated" | "unjudged";

export interface DiffResult {
  a: MetricCell | null;
  b: MetricCell | null;
  /** b − a. */
  delta: number | null;
  /** √(noise_floor_a² + noise_floor_b²) when both rows carry a floor: what |Δ| is judged against. */
  floor: number | null;
  /** What the delta was judged against: the combined floor, the two confidence intervals, or nothing. */
  noise: "floor" | "ci_overlap" | "none";
  verdict: DiffVerdict;
  /** What decided the verdict: the combined noise floor, overlapping CIs, an exact tie, the direction. */
  basis: "noise_floor" | "ci_overlap" | "exact" | "direction" | "none";
  /** Why the two numbers are not directly comparable; empty when they are. */
  notComparable: string[];
}

/** A stored number the comparison can use (Python `_finite`): null, NaN and ±∞ are not. */
const finite = (x: number | null): x is number => x !== null && Number.isFinite(x);

/**
 * Whether sampling noise explains the delta of two rows, and what it was judged against
 * (Python `_noise`, in its order): both floors, else four finite bounds, else nothing (`within` null).
 */
export function noiseOf(
  a: Pick<MetricCell, "noise_floor" | "ci_low" | "ci_high">,
  b: Pick<MetricCell, "noise_floor" | "ci_low" | "ci_high">,
  delta: number,
): { within: boolean | null; noise: DiffResult["noise"]; floor: number | null } {
  if (finite(a.noise_floor) && finite(b.noise_floor)) {
    const floor = Math.hypot(a.noise_floor, b.noise_floor);
    return { within: Math.abs(delta) <= floor, noise: "floor", floor };
  }
  if (finite(a.ci_low) && finite(a.ci_high) && finite(b.ci_low) && finite(b.ci_high)) {
    const overlap = Math.max(a.ci_low, b.ci_low) <= Math.min(a.ci_high, b.ci_high);
    return { within: overlap, noise: "ci_overlap", floor: null };
  }
  return { within: null, noise: "none", floor: null };
}

/** Reasons a pair of evaluations cannot be compared number for number (global part: versions). */
export function pairReasons(a: EvaluationSummary | undefined, b: EvaluationSummary | undefined): string[] {
  if (!a || !b) return [];
  const out: string[] = [];
  if (a.catalogue_version !== b.catalogue_version)
    out.push(`catalogue ${a.catalogue_version} vs ${b.catalogue_version}`);
  if (a.evaluator_version !== b.evaluator_version)
    out.push(`evaluator ${a.evaluator_version} vs ${b.evaluator_version}`);
  return out;
}

/** Tables whose encoding plan differs between evaluations i and j. */
export function encodingDiffers(comparison: Pick<Comparison, "comparability">, i: number, j: number): Set<string> {
  const out = new Set<string>();
  for (const plan of comparison.comparability.encoding_plans) {
    const a = plan.digests[i] ?? null;
    const b = plan.digests[j] ?? null;
    if (a !== null && b !== null && a !== b) out.add(plan.table_name);
  }
  return out;
}

/** The comparison of one metric between evaluations `i` (A) and `j` (B). */
export function diffMetric(
  metric: Pick<ComparedMetric, "metric_id" | "table_name" | "level" | "cells">,
  i: number,
  j: number,
  context: { versionReasons?: string[]; encodingTables?: Set<string> } = {},
): DiffResult {
  const a = metric.cells[i] ?? null;
  const b = metric.cells[j] ?? null;
  const notComparable = [...(context.versionReasons ?? [])];
  const tables = context.encodingTables ?? new Set<string>();
  if (a && b && a.encoding_plan_digest && b.encoding_plan_digest && a.encoding_plan_digest !== b.encoding_plan_digest) {
    notComparable.push(`encoding plan of ${metric.table_name} differs`);
  } else if (metric.level === "model" ? tables.size > 0 : tables.has(metric.table_name)) {
    notComparable.push(
      metric.level === "model"
        ? `encoding plan differs (${[...tables].sort().join(", ")})`
        : `encoding plan of ${metric.table_name} differs`,
    );
  }
  const base = { a, b, notComparable: [...new Set(notComparable)] };
  const none = { delta: null, floor: null, noise: "none", basis: "none" } as const;
  if (!a || !b) return { ...base, ...none, verdict: "missing" };
  if (a.status === "not_evaluated" || b.status === "not_evaluated")
    return { ...base, ...none, verdict: "not_evaluated" };
  // No delta without two finite values: a lift with no events is evaluated, and gates on its CI bound (Ruling R38).
  if (!finite(a.value) || !finite(b.value)) return { ...base, ...none, verdict: "unjudged" };
  const delta = b.value - a.value;
  const { within, noise, floor } = noiseOf(a, b, delta);
  const judged = { ...base, delta, floor, noise };
  if (delta === 0) return { ...judged, verdict: "same", basis: "exact" };
  if (within) return { ...judged, verdict: "approx", basis: noise === "floor" ? "noise_floor" : "ci_overlap" };
  if (base.notComparable.length) return { ...judged, verdict: "unjudged", basis: "none" };
  const meta = metricMeta(metric.metric_id);
  if (!meta) return { ...judged, verdict: "unjudged", basis: "none" };
  let worse: boolean;
  if (meta.direction === "lower_better") worse = delta > 0;
  else if (meta.direction === "higher_better") worse = delta < 0;
  else {
    if (meta.target === null) return { ...judged, verdict: "unjudged", basis: "none" };
    // Worse only when strictly farther from the target: the same distance on the other side reads better.
    worse = Math.abs(b.value - meta.target) > Math.abs(a.value - meta.target);
  }
  return { ...judged, verdict: worse ? "worse" : "better", basis: "direction" };
}

export const VERDICT_LABEL: Record<DiffVerdict, string> = {
  approx: "≈ within noise",
  better: "Better",
  worse: "Worse",
  same: "Same",
  missing: "Missing in one run",
  not_evaluated: "Not evaluated in one run",
  unjudged: "Not judged",
};

/** Non-dominated points when both axes are better-when-higher (ties keep both). */
export function paretoFrontier<T extends { x: number; y: number }>(points: readonly T[]): Set<T> {
  const frontier = new Set<T>();
  for (const p of points) {
    const dominated = points.some((q) => q !== p && q.x >= p.x && q.y >= p.y && (q.x > p.x || q.y > p.y));
    if (!dominated) frontier.add(p);
  }
  return frontier;
}

/** Stable colour slot per category value, in first-seen order (never by rank); past 8 → "other". */
export function slotMap(values: readonly (string | null | undefined)[], max = 8): Map<string, number> {
  const map = new Map<string, number>();
  for (const value of values) {
    const key = value ?? "—";
    if (!map.has(key)) map.set(key, map.size < max ? map.size + 1 : 0);
  }
  return map;
}
