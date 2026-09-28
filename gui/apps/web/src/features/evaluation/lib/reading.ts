/**
 * The honest reading of one metric row: the value, the score, the baseline,
 * the noise floor and the CI side by side, the number the status gate reads
 * (the value, or ci_low for `uses_ci_bound` metrics), and a sentence that
 * explains the status with the catalogue rule (inclusive thresholds, the
 * noise-floor exemption, warn 0 / fail 0, documented edges).
 *
 * The stored `status` is authoritative (the evaluator wrote it, possibly
 * under another catalogue version). The rule is re-read here only to
 * explain it; when the two disagree the explanation says so.
 */
import type { MetricRow } from "@contracts/api";
import { statusFor, type ScoringMetric } from "@synthetic-platform/stats/scoring";

import { metricMeta } from "./catalogue";
import { fmtMetric, fmtSig } from "./format";

export type Direction = "lower_better" | "higher_better" | "target";
export type GateSource = "value" | "ci_low" | "ci_high";

export interface Reading {
  value: number | null;
  score: number | null;
  status: string;
  baseline: number | null;
  noiseFloor: number | null;
  ciLow: number | null;
  ciHigh: number | null;
  warn: number | null;
  fail: number | null;
  direction: Direction;
  /** The value a perfect generator scores (target metrics read d = |v − t|). */
  target: number | null;
  /** What the status gate reads. */
  gate: number | null;
  gateSource: GateSource;
  /** The reference a FAIL must clear by more than the noise floor (target, 0 or the top of the range). */
  noiseRef: number;
  /** |gate − noiseRef| ≤ noiseFloor; null when there is no floor. */
  withinNoise: boolean | null;
  /** detail.reason (why not_evaluated). */
  reason: string | null;
  kind: string | null;
  /** The edge is documented (enforced: false): INFO, never FAIL. */
  documented: boolean;
  usesCi: boolean;
}

type Detail = { reason?: unknown; enforced?: unknown; role?: unknown; [key: string]: unknown };

export function detailOf(row: Pick<MetricRow, "detail">): Detail {
  const detail = row.detail;
  return detail && typeof detail === "object" && !Array.isArray(detail) ? detail : {};
}

export function reasonOf(row: Pick<MetricRow, "detail">): string | null {
  const reason = detailOf(row).reason;
  return typeof reason === "string" && reason.trim() ? reason : null;
}

export function isDocumentedEdge(row: Pick<MetricRow, "detail" | "level">): boolean {
  if (row.level !== "relationship") return false;
  const detail = detailOf(row);
  return detail.enforced === false || detail.role === "documented";
}

function directionOf(row: MetricRow): Direction {
  const meta = metricMeta(row.metric_id);
  if (meta) return meta.direction;
  return "lower_better";
}

/** Whether status and score read the CI bound: the catalogue says so, or (unknown id) the noise method is a rate ratio. */
function usesCiBound(row: MetricRow): boolean {
  const meta = metricMeta(row.metric_id);
  if (meta) return meta.uses_ci_bound;
  return row.noise_floor_method === "rate_ratio";
}

function targetOf(row: MetricRow, direction: Direction): number | null {
  const meta = metricMeta(row.metric_id);
  if (direction === "target") return meta?.target ?? row.source_value ?? 0;
  return meta?.target ?? null;
}

function scoringMetric(row: MetricRow): ScoringMetric {
  const meta = metricMeta(row.metric_id);
  return {
    id: row.metric_id,
    level: row.level,
    family: row.family,
    direction: directionOf(row),
    target: meta?.target ?? null,
    range: meta?.range ?? [0, null],
    // The row's thresholds win: they are what the evaluator applied.
    thresholds: { warn: row.threshold_warn, fail: row.threshold_fail },
    score: meta?.score ?? "none",
    uses_ci_bound: usesCiBound(row),
  };
}

export function readingOf(row: MetricRow): Reading {
  const direction = directionOf(row);
  const usesCi = usesCiBound(row);
  let gate = row.value;
  let gateSource: GateSource = "value";
  if (usesCi && row.value !== null) {
    const bound = direction === "higher_better" ? row.ci_high : row.ci_low;
    if (bound !== null) {
      gate = bound;
      gateSource = direction === "higher_better" ? "ci_high" : "ci_low";
    }
  }
  const target = targetOf(row, direction);
  const meta = metricMeta(row.metric_id);
  const noiseRef =
    direction === "target"
      ? (target ?? 0)
      : (meta?.target ?? (direction === "higher_better" ? (meta?.range[1] ?? 1) : 0));
  const withinNoise = row.noise_floor === null || gate === null ? null : Math.abs(gate - noiseRef) <= row.noise_floor;
  return {
    value: row.value,
    score: row.score,
    status: row.status,
    baseline: row.baseline_value,
    noiseFloor: row.noise_floor,
    ciLow: row.ci_low,
    ciHigh: row.ci_high,
    warn: row.threshold_warn,
    fail: row.threshold_fail,
    direction,
    target,
    gate,
    gateSource,
    noiseRef,
    withinNoise,
    reason: reasonOf(row),
    kind: row.value_kind,
    documented: isDocumentedEdge(row),
    usesCi,
  };
}

/** The status the catalogue rule gives for this row (to explain, never to override, the stored one). */
export function ruleStatus(row: MetricRow): string {
  return statusFor(scoringMetric(row), {
    value: row.value,
    ciLow: row.ci_low,
    ciHigh: row.ci_high,
    noiseFloor: row.noise_floor,
    sourceValue: row.source_value,
  });
}

const STATUS_WORD: Record<string, string> = {
  pass: "PASS",
  warn: "WARN",
  fail: "FAIL",
  info: "INFO",
  not_evaluated: "NOT EVALUATED",
};

export function statusWord(status: string): string {
  return STATUS_WORD[status] ?? status.toUpperCase();
}

/** One or two sentences: why this row has its status, in the catalogue's own terms. */
export function explainStatus(row: MetricRow, reading: Reading = readingOf(row)): string {
  const k = reading.kind;
  const f = (v: number | null) => fmtMetric(v, k);
  if (row.status === "not_evaluated" || reading.value === null) {
    return `Not evaluated: ${reading.reason ?? "the evaluator recorded no reason"}.`;
  }
  if (reading.documented) {
    return "Documented edge (enforced: false): the launch does not enforce it, so its orphan rate is reported as INFO, never as a FAIL.";
  }
  const { warn, fail, gate, direction } = reading;
  let text: string;
  const gateText =
    reading.gateSource === "value"
      ? `value ${f(gate)}`
      : `${reading.gateSource} ${f(gate)} (the gate reads the 95% CI bound, not the value ${f(reading.value)})`;
  if (warn === null && fail === null) {
    text = "No thresholds: this metric is informational.";
  } else if (gate === null) {
    text = "No value to gate.";
  } else if (warn === 0 && fail === 0) {
    text =
      gate > 0
        ? `${gateText} > 0 → FAIL: integrity holds by construction, so any violation fails.`
        : `${gateText} = 0 → PASS: integrity holds by construction.`;
  } else if (reading.withinNoise) {
    text = `${gateText} is within the noise floor ${f(reading.noiseFloor)} of ${f(reading.noiseRef)} → PASS: indistinguishable from sampling noise at this n.`;
  } else if (direction === "higher_better") {
    if (fail !== null && gate <= fail) text = `${gateText} ≤ fail ${f(fail)} (inclusive) → FAIL.`;
    else if (warn !== null && gate <= warn) text = `${gateText} ≤ warn ${f(warn)} (inclusive) → WARN.`;
    else text = `${gateText} > warn ${f(warn)} → PASS.`;
  } else if (direction === "target") {
    const d = Math.abs(gate - (reading.target ?? 0));
    const dText = `d = |${f(gate)} − ${fmtSig(reading.target)}| = ${fmtSig(d)}`;
    if (fail !== null && d >= fail) text = `${dText} ≥ fail ${fmtSig(fail)} (inclusive) → FAIL.`;
    else if (warn !== null && d >= warn) text = `${dText} ≥ warn ${fmtSig(warn)} (inclusive) → WARN.`;
    else text = `${dText} < warn ${fmtSig(warn)} → PASS.`;
  } else {
    if (fail !== null && gate >= fail) text = `${gateText} ≥ fail ${f(fail)} (inclusive) → FAIL.`;
    else if (warn !== null && gate >= warn) text = `${gateText} ≥ warn ${f(warn)} (inclusive) → WARN.`;
    else text = `${gateText} < warn ${f(warn)} → PASS.`;
  }
  if (
    reading.withinNoise === false &&
    reading.noiseFloor !== null &&
    (row.status === "fail" || row.status === "warn")
  ) {
    text += ` It clears the noise floor ${f(reading.noiseFloor)}.`;
  }
  const rule = ruleStatus(row);
  if (rule !== row.status && row.status !== "info") {
    text += ` The evaluator stored ${statusWord(row.status)}; this build's catalogue rule reads ${statusWord(rule)} — the stored status wins.`;
  }
  return text;
}
