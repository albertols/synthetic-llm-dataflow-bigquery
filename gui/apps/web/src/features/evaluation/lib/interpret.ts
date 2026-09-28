/**
 * The interpretation panel: sentences generated from each metric's catalogue
 * interpretation, its status, its noise floor and its reference baseline, e.g.
 *
 *   "KS 0.04 on orders.amount is below the noise floor 0.05 and near the
 *    10k-sample baseline 0.03 — indistinguishable at this n."
 *
 * Findings are ranked (FAIL, WARN, NOT EVALUATED, documented INFO) and point
 * at the view that holds the evidence.
 */
import type { EvaluationDetail, MetricRow } from "@contracts/api";

import { FAMILY_LABEL, isRollup, metricMeta, metricShort } from "./catalogue";
import { fmtMetric, fmtSampleSize, fmtScore, fmtSig, scopeLabel } from "./format";
import { countStatuses, metricKey, statusRank } from "./model";
import { readingOf, type Reading } from "./reading";

export type RunTab = "overview" | "columns" | "pairs" | "privacy" | "detection" | "relational" | "params";

export interface Finding {
  key: string;
  severity: string;
  level: string;
  family: string;
  metricId: string;
  text: string;
  /** Where the evidence lives. */
  target: { tab: RunTab; column?: string; table?: string };
}

/** Which tab carries the evidence for a row. */
export function tabFor(row: Pick<MetricRow, "level" | "family" | "metric_id">): RunTab {
  if (row.level === "field" || row.level === "column") return "columns";
  if (row.level === "pair") return "pairs";
  if (row.level === "relationship") return "relational";
  if (row.metric_id === "table.detection_auc" || row.metric_id === "table.pmse_ratio") return "detection";
  if (row.family === "privacy") return "privacy";
  if (row.metric_id === "table.corr_rms_delta" || row.metric_id === "table.corr_max_delta") return "pairs";
  return "overview";
}

function firstSentence(text: string | undefined): string {
  if (!text) return "";
  // A sentence ends at . ! ? followed by space or the end — not inside "e.g." / "i.e." / "vs.".
  const match = /^(.+?(?<!\be\.g|\bi\.e|\bvs)[.!?])(\s|$)/.exec(text.trim());
  return (match?.[1] ?? text).trim();
}

function noisePhrase(reading: Reading): string | null {
  if (reading.noiseFloor === null || reading.gate === null) return null;
  const floor = fmtMetric(reading.noiseFloor, reading.kind);
  if (reading.gateSource !== "value") return null;
  return reading.withinNoise ? `below the noise floor ${floor}` : `above the noise floor ${floor}`;
}

function baselinePhrase(reading: Reading, referenceN: number | null | undefined): string | null {
  const { baseline, value } = reading;
  if (baseline === null || value === null) return null;
  const size = referenceN ? `${fmtSampleSize(referenceN)}-sample ` : "";
  const b = fmtMetric(baseline, reading.kind);
  const tolerance = Math.max(reading.noiseFloor ?? 0, 0.25 * Math.abs(baseline), 1e-12);
  if (Math.abs(value - baseline) <= tolerance) return `near the ${size}baseline ${b}`;
  if (reading.direction === "lower_better" && baseline > 0 && value > baseline) {
    const ratio = value / baseline;
    return ratio >= 2
      ? `${ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1)}× the ${size}baseline ${b}`
      : `above the ${size}baseline ${b}`;
  }
  if (reading.direction === "lower_better" && value > baseline) return `above the ${size}baseline ${b}`;
  if (reading.direction === "higher_better" && value < baseline) return `below the ${size}baseline ${b}`;
  if (reading.direction === "target") return `off target where the ${size}baseline is ${b}`;
  return `better than the ${size}baseline ${b}`;
}

function ciPhrase(reading: Reading): string | null {
  if (!reading.usesCi || reading.ciLow === null) return null;
  const lo = fmtMetric(reading.ciLow, reading.kind);
  const hi = reading.ciHigh === null ? "∞" : fmtMetric(reading.ciHigh, reading.kind);
  return `95% CI ${lo}–${hi}; the gate reads ci_low ${lo}`;
}

/** The crossed threshold in the metric's own terms: "≥ 0.2", "≤ 70%", "|v − 1| ≥ 0.4". */
function thresholdText(reading: Reading, which: "warn" | "fail"): string {
  const t = which === "warn" ? reading.warn : reading.fail;
  const f = (v: number | null) => fmtMetric(v, reading.kind);
  if (t === null) return `${which} threshold unset`;
  if (reading.warn === 0 && reading.fail === 0) return "any value above 0 fails";
  const gate = reading.gateSource === "value" ? "value" : reading.gateSource;
  if (reading.direction === "higher_better") return `${gate} ≤ ${which} ${f(t)}`;
  if (reading.direction === "target") {
    const d = reading.gate === null ? null : Math.abs(reading.gate - (reading.target ?? 0));
    return `|${gate} − ${fmtSig(reading.target)}| = ${fmtSig(d)} ≥ ${which} ${fmtSig(t)}`;
  }
  return `${gate} ≥ ${which} ${f(t)}`;
}

/** One sentence (two with the catalogue's reading) for a metric row. */
export function interpretRow(row: MetricRow, referenceN?: number | null, orphanSource?: number | null): string {
  const reading = readingOf(row);
  const name = metricShort(row.metric_id);
  const scope = scopeLabel(row);
  const meta = metricMeta(row.metric_id);
  if (row.status === "not_evaluated" || reading.value === null) {
    return `${name} on ${scope} was not evaluated: ${reading.reason ?? "no reason recorded"}. Not evaluated is not a pass.`;
  }
  const value = fmtMetric(reading.value, reading.kind);
  if (reading.documented) {
    const source =
      orphanSource === null || orphanSource === undefined
        ? ""
        : `; the source's own orphan rate is ${fmtMetric(orphanSource, "share")}`;
    return `${name} ${value} on ${scope} is informational: the edge is documented (enforced: false), so it never fails${source}.`;
  }
  const parts = [noisePhrase(reading), baselinePhrase(reading, referenceN)].filter((p): p is string => !!p);
  const ci = ciPhrase(reading);
  const middle = parts.length ? ` is ${parts.join(" and ")}` : "";
  const bracket = ci ? ` (${ci})` : "";
  let verdict: string;
  const status: string = row.status;
  switch (status) {
    case "pass":
      verdict = reading.withinNoise
        ? "indistinguishable at this n."
        : firstSentence(meta?.interpretation.good) || "within the warn threshold.";
      break;
    case "warn":
      verdict = `WARN (${thresholdText(reading, "warn")}). ${firstSentence(meta?.interpretation.bad)}`.trim();
      break;
    case "fail":
      verdict = `FAIL (${thresholdText(reading, "fail")}). ${firstSentence(meta?.interpretation.bad)}`.trim();
      break;
    case "info":
      verdict = "informational (no thresholds).";
      break;
    default:
      verdict = `status “${status}” (not in this build's vocabulary).`;
  }
  return `${name} ${value} on ${scope}${middle}${bracket} — ${verdict}`;
}

function referenceNFor(detail: Pick<EvaluationDetail, "evaluation">, table: string): number | null {
  return detail.evaluation.tables.find((t) => t.name === table)?.reference_n ?? null;
}

/** Ranked findings: FAIL, WARN, NOT EVALUATED, then documented edges (INFO). */
export function findings(detail: Pick<EvaluationDetail, "evaluation" | "metrics">): Finding[] {
  const orphanSource = new Map<string, number | null>();
  for (const row of detail.metrics)
    if (row.metric_id === "relationship.orphan_rate_source" && row.edge) orphanSource.set(row.edge, row.value);
  const out: Finding[] = [];
  for (const row of detail.metrics) {
    if (isRollup(row.metric_id)) continue;
    const reading = readingOf(row);
    const notable =
      row.status === "fail" ||
      row.status === "warn" ||
      row.status === "not_evaluated" ||
      (row.metric_id === "relationship.orphan_rate" && reading.documented) ||
      statusRank(row.status) === 2.5;
    if (!notable) continue;
    const tab = tabFor(row);
    out.push({
      key: metricKey(row),
      severity: row.status,
      level: row.level,
      family: row.family,
      metricId: row.metric_id,
      text: interpretRow(row, referenceNFor(detail, row.table_name), row.edge ? orphanSource.get(row.edge) : null),
      target: {
        tab,
        table: row.level === "model" ? undefined : row.table_name,
        column: tab === "columns" && row.column_name ? `${row.table_name}.${row.column_name}` : undefined,
      },
    });
  }
  const levelOrder = ["row", "table", "relationship", "field", "column", "pair", "model"];
  const familyOrder = ["privacy", "integrity", "fidelity", "diversity"];
  const familyRank = (f: string) => (familyOrder.includes(f) ? familyOrder.indexOf(f) : familyOrder.length);
  return out.sort(
    (a, b) =>
      statusRank(a.severity) - statusRank(b.severity) ||
      familyRank(a.family) - familyRank(b.family) ||
      levelOrder.indexOf(a.level) - levelOrder.indexOf(b.level) ||
      a.key.localeCompare(b.key),
  );
}

/** The run in two or three sentences: the verdict, where the problems concentrate, what did not run. */
export function headline(detail: Pick<EvaluationDetail, "evaluation" | "metrics">): string {
  const { evaluation, metrics } = detail;
  if (!metrics.length) {
    if (evaluation.status === "RUNNING") return "The evaluation is still running: no metrics have been written yet.";
    return `No metrics were written${evaluation.status_reason ? `: ${evaluation.status_reason}` : "."}`;
  }
  const measured = metrics.filter((m) => !isRollup(m.metric_id));
  const counts = countStatuses(measured);
  const overall = metrics.find((m) => m.metric_id === "model.overall_score")?.value ?? evaluation.overall_score;
  const parts = [
    `Overall score ${fmtScore(overall)} over ${measured.length.toLocaleString("en-US")} measured metrics: ${counts.fail} fail, ${counts.warn} warn, ${counts.pass} pass${counts.not_evaluated ? `, ${counts.not_evaluated} not evaluated` : ""}.`,
  ];
  const failing = measured.filter((m) => m.status === "fail");
  if (failing.length) {
    const byFamily = new Map<string, number>();
    const byLevel = new Map<string, number>();
    for (const m of failing) {
      byFamily.set(m.family, (byFamily.get(m.family) ?? 0) + 1);
      byLevel.set(m.level, (byLevel.get(m.level) ?? 0) + 1);
    }
    const [family, nf] = [...byFamily.entries()].sort((a, b) => b[1] - a[1])[0]!;
    const [level] = [...byLevel.entries()].sort((a, b) => b[1] - a[1])[0]!;
    parts.push(
      `Failures concentrate in ${FAMILY_LABEL[family]?.toLowerCase() ?? family} (${nf} of ${failing.length}), mostly at the ${level} level.`,
    );
  }
  const privacyNotEvaluated = measured.filter((m) => m.family === "privacy" && m.status === "not_evaluated").length;
  if (privacyNotEvaluated)
    parts.push(`${privacyNotEvaluated} privacy metrics did not run — privacy is unproven here, not passed.`);
  return parts.join(" ");
}
