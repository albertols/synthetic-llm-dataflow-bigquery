/**
 * The honest reading of one metric row: the value, the score, the baseline,
 * the noise floor and the CI side by side, the number the status gate reads
 * (the value, or its CI bound for `uses_ci_bound` metrics — even when the
 * point value is undefined, Ruling R38), what the noise check compared and
 * what the evaluator recorded about it (a noise downgrade, a missing input,
 * an infinite value), and a sentence that explains the status with the
 * evaluator's rule (`@synthetic-platform/stats/scoring`, the golden-pinned
 * port of `sdfb_evaluation.scoring`).
 *
 * The stored `status` is authoritative (the evaluator wrote it, possibly
 * under another catalogue version). The rule is re-read here only to
 * explain it; when the two disagree the explanation says so.
 */
import type { MetricRow } from "@contracts/api";
import {
  copyRateInfoReason,
  INTERVAL_NOISE_METHODS,
  noiseReference,
  reached,
  SCALAR_NOISE_METHODS,
  statusFor,
  type MetricReading,
  type ScoringMetric,
} from "@synthetic-platform/stats/scoring";

import { metricMeta } from "./catalogue";
import { fmtCompared, fmtMetric, fmtSig } from "./format";

export type Direction = "lower_better" | "higher_better" | "target";
export type GateSource = "value" | "ci_low" | "ci_high";
/** How the noise check decides (Ruling R41): a scalar floor, CI coverage, or no check. */
export type NoiseKind = "scalar" | "interval" | "none";

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
  /** What the status gate reads; ±Infinity when detail.nonfinite says the stored null was infinite. */
  gate: number | null;
  gateSource: GateSource;
  /** What the noise check compares with: the target, else 0 (1 when higher is better). */
  noiseRef: number;
  noiseMethod: string | null;
  noiseKind: NoiseKind;
  /** Scalar: |gate − noiseRef| ≤ noiseFloor; interval: the CI covers noiseRef; null without the input or a check. */
  withinNoise: boolean | null;
  /** detail.noise_downgraded_from: the status sampling noise explained away (Ruling R40). */
  downgradedFrom: "warn" | "fail" | null;
  /** detail.noise_check = "unavailable": the check had no floor or CI, so nothing was downgraded (R41). */
  noiseUnavailable: boolean;
  /** detail.nonfinite: the gated number was infinite past the bad side (R43). */
  nonfinite: "+inf" | "-inf" | null;
  /** The point value is undefined (a lift with no events) while the gate reads its bound (R38). */
  valueUndefined: boolean;
  /**
   * A Wilson-interval metric whose reference is its range edge — an adherence share (1) or a
   * copy / match rate (0), R45's metrics — so an observed crossing is never downgraded as noise.
   * Newcombe deltas also sit at 0 but ARE downgraded when their folded CI reaches it.
   */
  edgeReference: boolean;
  /** detail.reason (why not_evaluated). */
  reason: string | null;
  kind: string | null;
  /** The orphan rate of a documented edge (enforced: false): INFO, never FAIL (R42). */
  documented: boolean;
  /**
   * The copy rate of a column that is not free text: reported, never gated (R66). The evaluator's
   * own reason (detail.reason), or the rule's when the row carries none.
   */
  ungated: string | null;
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

/** "fail" / "warn" when the evaluator downgraded this row's crossing as sampling noise (R40). */
export function downgradedFrom(row: Pick<MetricRow, "detail">): "warn" | "fail" | null {
  const from = detailOf(row).noise_downgraded_from;
  return from === "warn" || from === "fail" ? from : null;
}

/** The row belongs to a documented foreign-key edge (enforced: false), whatever its metric. */
export function onDocumentedEdge(row: Pick<MetricRow, "detail" | "level">): boolean {
  if (row.level !== "relationship") return false;
  const detail = detailOf(row);
  return detail.enforced === false || detail.role === "documented";
}

/**
 * The row reads as documented INFO: only the ORPHAN RATE of a documented edge (Ruling R42) — the
 * edge's fan-out rows compare children per parent with the source and stay graded. The producer's
 * detail says the edge is documented, or the rule does: the orphan rate is zero-tolerance, so the
 * evaluator writes it as INFO only on a documented edge.
 */
export function isDocumentedEdge(row: Pick<MetricRow, "detail" | "level" | "metric_id" | "status">): boolean {
  if (row.level !== "relationship" || row.metric_id !== "relationship.orphan_rate") return false;
  return onDocumentedEdge(row) || row.status === "info";
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
  if (direction === "target") return meta?.target ?? row.source_value ?? null;
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
    // The row's thresholds and noise method win: they are what the evaluator applied.
    thresholds: { warn: row.threshold_warn, fail: row.threshold_fail },
    score: meta?.score ?? "none",
    uses_ci_bound: usesCiBound(row),
    noise_floor: row.noise_floor_method,
  };
}

function nonfiniteOf(row: MetricRow): "+inf" | "-inf" | null {
  const sign = detailOf(row).nonfinite;
  return sign === "+inf" || sign === "-inf" ? sign : null;
}

/** The producer's numbers as the scorer saw them: a stored null that detail.nonfinite marks is ±Infinity again. */
function scorerReading(row: MetricRow, gateSource: GateSource): MetricReading {
  const sign = nonfiniteOf(row);
  const inf = sign === "+inf" ? Infinity : -Infinity;
  const restore = (field: GateSource, stored: number | null) =>
    sign && stored === null && gateSource === field ? inf : stored;
  return {
    value: restore("value", row.value),
    ciLow: restore("ci_low", row.ci_low),
    ciHigh: restore("ci_high", row.ci_high),
    noiseFloor: row.noise_floor,
    sourceValue: row.source_value,
    detail: detailOf(row),
    columnKind: row.column_kind,
  };
}

/** Why a copy rate is INFO whatever its value, or null when it is gated (its column is free text). */
function ungatedReason(row: MetricRow): string | null {
  if (row.metric_id !== "field.substantive_copy_rate") return null;
  const rule = copyRateInfoReason(row.column_kind);
  return rule === null ? null : (reasonOf(row) ?? rule);
}

export function readingOf(row: MetricRow): Reading {
  const direction = directionOf(row);
  const usesCi = usesCiBound(row);
  const gateSource: GateSource = !usesCi ? "value" : direction === "higher_better" ? "ci_high" : "ci_low";
  const scorer = scorerReading(row, gateSource);
  const gate = (gateSource === "value" ? scorer.value : gateSource === "ci_low" ? scorer.ciLow : scorer.ciHigh) ?? null;
  const target = targetOf(row, direction);
  const meta = metricMeta(row.metric_id);
  const noiseRef = noiseReference({ direction, target: meta?.target ?? null }, direction === "target" ? target : null);
  const noiseMethod = row.noise_floor_method;
  const noiseKind: NoiseKind =
    noiseMethod && SCALAR_NOISE_METHODS.has(noiseMethod)
      ? "scalar"
      : noiseMethod && INTERVAL_NOISE_METHODS.has(noiseMethod)
        ? "interval"
        : "none";
  let withinNoise: boolean | null = null;
  if (noiseKind === "scalar" && row.noise_floor !== null && gate !== null && Number.isFinite(gate))
    withinNoise = Math.abs(gate - noiseRef) <= row.noise_floor;
  if (noiseKind === "interval" && row.ci_low !== null && row.ci_high !== null)
    withinNoise = row.ci_low <= noiseRef && noiseRef <= row.ci_high;
  const range = meta?.range ?? [0, null];
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
    noiseMethod,
    noiseKind,
    withinNoise,
    downgradedFrom: downgradedFrom(row),
    noiseUnavailable: detailOf(row).noise_check === "unavailable",
    nonfinite: nonfiniteOf(row),
    valueUndefined: usesCi && row.value === null && gate !== null,
    edgeReference: noiseMethod === "wilson" && (noiseRef === range[0] || noiseRef === range[1]),
    reason: reasonOf(row),
    kind: row.value_kind,
    documented: isDocumentedEdge(row),
    ungated: ungatedReason(row),
    usesCi,
  };
}

/** The status the catalogue rule gives for this row (to explain, never to override, the stored one). */
export function ruleStatus(row: MetricRow): string {
  const reading = readingOf(row);
  return statusFor(scoringMetric(row), scorerReading(row, reading.gateSource), { enforced: !reading.documented });
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

/** "≈ within noise, was FAIL" — the marker a noise-downgraded row carries everywhere it is listed. */
export function downgradeLabel(from: "warn" | "fail"): string {
  return `≈ within noise, was ${statusWord(from)}`;
}

/**
 * Why a CI-bound metric has no point value while its gate still reads the bound (R38). The bound
 * tells: a rate-ratio interval starts at 0 only with no copies at all (m_R = m_H = 0); a positive
 * lower bound means copies in R and none in the holdout, an infinite ratio stored as NULL.
 */
export function undefinedValueText(reading: Pick<Reading, "ciLow">): string {
  return reading.ciLow === 0
    ? "undefined: no copies on either side"
    : "infinite: copies in the reference sample, none in the holdout";
}

function gateTextOf(reading: Reading, gateNumber: string): string {
  if (reading.gateSource === "value") return `value ${gateNumber}`;
  const value = reading.valueUndefined
    ? `the value is ${undefinedValueText(reading)}`
    : `not the value ${fmtMetric(reading.value, reading.kind)}`;
  return `${reading.gateSource} ${gateNumber} (the gate reads the 95% CI bound; ${value})`;
}

/** The threshold crossing alone (no noise): its sentence and the status it implies. */
function crossing(reading: Reading): { text: string; status: "pass" | "warn" | "fail" } | null {
  const { warn, fail, gate, direction } = reading;
  if (gate === null) return null;
  // The evaluator's crossing: inclusive, with its 1e-9 relative tolerance (|0.9 − 1| reaches 0.1).
  // Numbers print with enough digits to show the comparison they make (fmtCompared).
  if (direction === "target") {
    const d = Math.abs(gate - (reading.target ?? 0));
    const [g, t] = fmtCompared([gate, reading.target], reading.kind);
    const [dt, w, f] = fmtCompared([d, warn, fail]);
    const dText = `d = |${g} − ${t}| = ${dt}`;
    if (reached(d, fail, false)) return { text: `${dText} ≥ fail ${f} (inclusive)`, status: "fail" };
    if (reached(d, warn, false)) return { text: `${dText} ≥ warn ${w} (inclusive)`, status: "warn" };
    return { text: `${dText} < warn ${w}`, status: "pass" };
  }
  const [g, w, f] = fmtCompared([gate, warn, fail], reading.kind);
  const gateText = gateTextOf(reading, g!);
  if (direction === "higher_better") {
    if (reached(gate, fail, true)) return { text: `${gateText} ≤ fail ${f} (inclusive)`, status: "fail" };
    if (reached(gate, warn, true)) return { text: `${gateText} ≤ warn ${w} (inclusive)`, status: "warn" };
    return { text: `${gateText} > warn ${w}`, status: "pass" };
  }
  if (reached(gate, fail, false)) return { text: `${gateText} ≥ fail ${f} (inclusive)`, status: "fail" };
  if (reached(gate, warn, false)) return { text: `${gateText} ≥ warn ${w} (inclusive)`, status: "warn" };
  return { text: `${gateText} < warn ${w}`, status: "pass" };
}

/** "scored as no effect (1.0)": a downgraded row's stored score, the score at the reference (R40). */
export function noEffectText(score: number | null): string {
  if (score === null) return "scored as no effect";
  return `scored as no effect (${Number.isInteger(score) ? score.toFixed(1) : fmtSig(score)})`;
}

/** What made the crossing sampling noise (or not), in the check's own terms. */
function noiseText(reading: Reading): string {
  if (reading.noiseKind === "interval") {
    const [lo, hi, ref] = fmtCompared([reading.ciLow, reading.ciHigh, reading.noiseRef], reading.kind);
    const upper = reading.ciHigh === null ? "∞" : hi;
    return reading.withinNoise
      ? `its 95% CI ${lo}–${upper} covers the reference ${ref}`
      : `its 95% CI ${lo}–${upper} excludes the reference ${ref}`;
  }
  const distance = reading.gate === null ? null : Math.abs(reading.gate - reading.noiseRef);
  const [floor, ref] = fmtCompared([reading.noiseFloor, reading.noiseRef, distance], reading.kind);
  return reading.withinNoise ? `it is within the noise floor ${floor} of ${ref}` : `it clears the noise floor ${floor}`;
}

/** One or two sentences: why this row has its status, in the catalogue's own terms. */
export function explainStatus(row: MetricRow, reading: Reading = readingOf(row)): string {
  if (row.status === "not_evaluated") {
    return `Not evaluated: ${reading.reason ?? "the evaluator recorded no reason"}.`;
  }
  if (reading.documented) {
    return "Documented edge (enforced: false): the launch does not enforce it, so its orphan rate is reported as INFO, never as a FAIL.";
  }
  if (reading.ungated && row.status === "info") {
    return `INFO on a ${row.column_kind} column, whatever the rate — ${reading.ungated}.`;
  }
  const { warn, fail, gate } = reading;
  let text: string;
  const cross = crossing(reading);
  if (warn === null && fail === null) {
    text = "No thresholds: this metric is informational.";
  } else if (reading.nonfinite) {
    const where = reading.gateSource === "value" ? "value" : reading.gateSource;
    text = `${where} ${reading.nonfinite === "+inf" ? "+∞" : "−∞"} lies past the bad side of every threshold → FAIL.`;
  } else if (gate === null || cross === null) {
    text = "No value to gate.";
  } else if (warn === 0 && fail === 0) {
    const gateText = gateTextOf(reading, fmtCompared([gate, 0], reading.kind)[0]!);
    text =
      gate > 0
        ? `${gateText} > 0 → FAIL: integrity holds by construction, so any violation fails, whatever the noise.`
        : `${gateText} = 0 → PASS: integrity holds by construction.`;
  } else if (reading.downgradedFrom) {
    const from = statusWord(reading.downgradedFrom);
    text = `${cross.text} would be ${from}, but ${noiseText(reading)} → PASS (${downgradeLabel(reading.downgradedFrom)}): indistinguishable from sampling noise at this n, so it is ${noEffectText(row.score)}.`;
  } else if (row.status === "pass" && cross.status !== "pass" && reading.withinNoise) {
    // A row written before the evaluator recorded its downgrades.
    text = `${cross.text}, but ${noiseText(reading)} → PASS: indistinguishable from sampling noise at this n.`;
  } else {
    text = `${cross.text} → ${statusWord(cross.status)}.`;
    if (cross.status !== "pass") {
      if (reading.noiseUnavailable) {
        text += ` The noise check had no ${reading.noiseKind === "interval" ? "confidence interval" : "noise floor"}, so nothing was downgraded.`;
      } else if (reading.withinNoise === false) {
        text += ` ${noiseText(reading).replace(/^i/, "I")}.`;
        if (reading.edgeReference)
          text +=
            " An observed copy or out-of-vocabulary value is an event, not an estimate: a Wilson interval never reaches this edge reference, so the metric is never downgraded as noise.";
      }
    }
  }
  const rule = ruleStatus(row);
  if (rule !== row.status && row.status !== "info") {
    text += ` The evaluator stored ${statusWord(row.status)}; this build's catalogue rule reads ${statusWord(rule)} — the stored status wins.`;
  }
  return text;
}
