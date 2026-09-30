/**
 * Noise-aware comparison of two evaluations (the A/B diff) and the
 * comparability rules.
 *
 * - A delta smaller than the larger of the two noise floors — or, for
 *   metrics that carry confidence intervals, two overlapping intervals — is
 *   "≈": it cannot be told apart from sampling noise, so it is never called
 *   better or worse.
 * - Two runs are not comparable when their catalogue or evaluator versions
 *   differ, or (per table) their encoding_plan_digest differs; the delta is
 *   still shown, flagged, and never judged.
 */
import type { ComparedMetric, Comparison, EvaluationSummary, MetricCell } from "@contracts/api";

import { metricMeta } from "./catalogue";

export type DiffVerdict = "approx" | "better" | "worse" | "same" | "missing" | "not_evaluated" | "unjudged";

export interface DiffResult {
  a: MetricCell | null;
  b: MetricCell | null;
  /** b − a. */
  delta: number | null;
  /** max(noise_floor_a, noise_floor_b), when either has one. */
  floor: number | null;
  verdict: DiffVerdict;
  /** What decided "≈": the noise floor, overlapping CIs, or an exact tie. */
  basis: "noise_floor" | "ci_overlap" | "exact" | "direction" | "none";
  /** Why the two numbers are not directly comparable; empty when they are. */
  notComparable: string[];
}

const EPS = 1e-12;

function ciOf(cell: MetricCell): [number, number] | null {
  if (cell.ci_low === null) return null;
  return [cell.ci_low, cell.ci_high ?? Number.POSITIVE_INFINITY];
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
  if (!a || !b) return { ...base, delta: null, floor: null, verdict: "missing", basis: "none" };
  if (a.status === "not_evaluated" || b.status === "not_evaluated")
    return { ...base, delta: null, floor: null, verdict: "not_evaluated", basis: "none" };
  // Evaluated without a point value (a lift with no events gates on its CI bound, Ruling R38): no delta to judge.
  if (a.value === null || b.value === null)
    return { ...base, delta: null, floor: null, verdict: "unjudged", basis: "none" };
  const delta = b.value - a.value;
  const floors = [a.noise_floor, b.noise_floor].filter((f): f is number => f !== null && Number.isFinite(f));
  const floor = floors.length ? Math.max(...floors) : null;
  if (Math.abs(delta) <= EPS) return { ...base, delta, floor, verdict: "same", basis: "exact" };
  // Inclusive, with a relative epsilon: 0.05 − 0.03 must read as the floor 0.02, not 0.020000000000000004.
  if (floor !== null && Math.abs(delta) <= floor * (1 + 1e-9) + EPS)
    return { ...base, delta, floor, verdict: "approx", basis: "noise_floor" };
  const ciA = ciOf(a);
  const ciB = ciOf(b);
  if (ciA && ciB && ciA[0] <= ciB[1] && ciB[0] <= ciA[1])
    return { ...base, delta, floor, verdict: "approx", basis: "ci_overlap" };
  if (base.notComparable.length) return { ...base, delta, floor, verdict: "unjudged", basis: "none" };
  const meta = metricMeta(metric.metric_id);
  if (!meta) return { ...base, delta, floor, verdict: "unjudged", basis: "none" };
  let better: boolean;
  if (meta.direction === "lower_better") better = delta < 0;
  else if (meta.direction === "higher_better") better = delta > 0;
  else {
    if (meta.target === null) return { ...base, delta, floor, verdict: "unjudged", basis: "none" };
    better = Math.abs(b.value - meta.target) < Math.abs(a.value - meta.target);
  }
  return { ...base, delta, floor, verdict: better ? "better" : "worse", basis: "direction" };
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
