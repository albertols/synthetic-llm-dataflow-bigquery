/**
 * The interpretation panel: sentences generated from each metric's catalogue
 * interpretation, its status, its noise floor and its reference baseline, e.g.
 *
 *   "KS 0.04 on orders.amount is below the noise floor 0.05 and near the
 *    10k-sample baseline 0.03 — indistinguishable at this n."
 *
 * Findings are ranked (FAIL, WARN, NOT EVALUATED, documented INFO, then the
 * PASS rows the evaluator downgraded as sampling noise — "≈ within noise, was
 * FAIL", Ruling R40, and last the key checks with no key declared, grouped:
 * not applicable, ADR 0042) and point at the view that holds the evidence. A PK
 * duplicate rate on a key the launch adjusted reads the generator's verdict
 * instead (ADR 0038, lib/adjustment.ts), between the real FAILs and the INFO.
 */
import type { TableModelAdjustment } from "@contracts/adjustments";
import type { EvaluationDetail, MetricRow } from "@contracts/api";
import { inapplicableKey, type InapplicableKey } from "@contracts/applicability";

import { isStale, STALE_AFTER_HOURS } from "@/lib/runState";

import { adjustmentFor, PK_METRIC, readAdjustedPk, type AdjustedReading } from "./adjustment";
import { FAMILY_LABEL, isRollup, metricMeta, metricShort } from "./catalogue";
import { fmtCompared, fmtMetric, fmtSampleSize, fmtScore, fmtSig, scopeLabel } from "./format";
import { countsPhrase, countStatuses, countsTotal, metricKey, statusRank } from "./model";
import { downgradeLabel, noEffectText, readingOf, undefinedValueText, type Reading } from "./reading";

export type RunTab = "overview" | "columns" | "pairs" | "privacy" | "detection" | "relational" | "params";

export interface Finding {
  key: string;
  severity: string;
  level: string;
  family: string;
  metricId: string;
  text: string;
  /** The status sampling noise explained away (a PASS finding), else null. */
  downgradedFrom: "warn" | "fail" | null;
  /**
   * The generator's reading of this table's declared key (ADR 0038), else null. It carries the
   * evaluator's own status: `severity` above is the finding's, not the stored row's.
   */
  adjusted: AdjustedReading | null;
  /** A key check with no key declared, for these tables (ADR 0042): one finding per kind, ranked last. */
  notApplicable?: { key: InapplicableKey; tables: string[] };
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

/** The noise check in the metric's own terms: a scalar floor, or a CI against the reference (Ruling R41). */
function noisePhrase(reading: Reading): string | null {
  if (reading.withinNoise === null) return null;
  if (reading.noiseKind === "interval") {
    const ref = fmtMetric(reading.noiseRef, reading.kind);
    return reading.withinNoise ? `with a 95% CI that covers ${ref}` : `with a 95% CI that excludes ${ref}`;
  }
  if (reading.gateSource !== "value") return null;
  const floor = fmtMetric(reading.noiseFloor, reading.kind);
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
  if (!reading.usesCi || reading.gate === null) return null;
  const lo = reading.ciLow === null ? "—" : fmtMetric(reading.ciLow, reading.kind);
  const hi = reading.ciHigh === null ? "∞" : fmtMetric(reading.ciHigh, reading.kind);
  return `95% CI ${lo}–${hi}; the gate reads ${reading.gateSource} ${fmtMetric(reading.gate, reading.kind)}`;
}

/**
 * The crossed threshold in the metric's own terms: "≥ 0.2", "≤ 99.99%", "|v − 1| ≥ 0.4" — with
 * enough digits to show the comparison (fmtCompared), never "≤ warn 100%" for 99.99%.
 */
function thresholdText(reading: Reading, which: "warn" | "fail"): string {
  const t = which === "warn" ? reading.warn : reading.fail;
  if (t === null) return `${which} threshold unset`;
  if (reading.warn === 0 && reading.fail === 0) return "any value above 0 fails";
  const gate = reading.gateSource === "value" ? "value" : reading.gateSource;
  if (reading.direction === "target") {
    const d = reading.gate === null ? null : Math.abs(reading.gate - (reading.target ?? 0));
    const [dt, tt] = fmtCompared([d, t]);
    return `|${gate} − ${fmtSig(reading.target)}| = ${dt} ≥ ${which} ${tt}`;
  }
  const [, tt] = fmtCompared([reading.gate, t], reading.kind);
  return reading.direction === "higher_better" ? `${gate} ≤ ${which} ${tt}` : `${gate} ≥ ${which} ${tt}`;
}

/** One sentence (two with the catalogue's reading) for a metric row. */
export function interpretRow(row: MetricRow, referenceN?: number | null, orphanSource?: number | null): string {
  const reading = readingOf(row);
  const name = metricShort(row.metric_id);
  const scope = scopeLabel(row);
  const meta = metricMeta(row.metric_id);
  if (row.status === "not_evaluated") {
    return `${name} on ${scope} was not evaluated: ${reading.reason ?? "no reason recorded"}. Not evaluated is not a pass.`;
  }
  // A lift without a finite point value still gates on its CI bound (Ruling R38); ci_low says why.
  const value = reading.valueUndefined
    ? `(${undefinedValueText(reading)})`
    : fmtCompared([reading.value, reading.warn, reading.fail], reading.kind)[0]!;
  if (reading.documented) {
    const source =
      orphanSource === null || orphanSource === undefined
        ? ""
        : `; the source's own orphan rate is ${fmtMetric(orphanSource, "share")}`;
    return `${name} ${value} on ${scope} is informational: the edge is documented (enforced: false), so it never fails${source}.`;
  }
  if (reading.ungated && row.status === "info") {
    return `${name} ${value} on ${scope} is informational: the copy rate is gated only on free text, and this column is ${row.column_kind}.`;
  }
  const parts = [noisePhrase(reading), baselinePhrase(reading, referenceN)].filter((p): p is string => !!p);
  const ci = ciPhrase(reading);
  const middle = parts.length ? ` is ${parts.join(" and ")}` : "";
  const bracket = ci ? ` (${ci})` : "";
  let verdict: string;
  const status: string = row.status;
  switch (status) {
    case "pass":
      verdict = reading.downgradedFrom
        ? `${downgradeLabel(reading.downgradedFrom)}: indistinguishable at this n, ${noEffectText(row.score)}.`
        : reading.withinNoise
          ? "indistinguishable at this n."
          : firstSentence(meta?.interpretation.good) || "within the warn threshold.";
      break;
    case "warn":
    case "fail": {
      const word = status === "warn" ? "WARN" : "FAIL";
      const why = reading.nonfinite
        ? `${reading.gateSource === "value" ? "value" : reading.gateSource} ${reading.nonfinite === "+inf" ? "+∞" : "−∞"}, past the bad side`
        : thresholdText(reading, status);
      const unavailable = reading.noiseUnavailable ? " No noise check: its floor or CI is missing." : "";
      verdict = `${word} (${why}).${unavailable} ${firstSentence(meta?.interpretation.bad)}`.trim();
      break;
    }
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

const NA_SUBJECT: Record<InapplicableKey, { one: string; many: string; check: string }> = {
  pk: { one: "declares no primary key", many: "declare no primary key", check: "uniqueness check" },
  identity: { one: "declares no identity columns", many: "declare no identity columns", check: "identity check" },
};

/** The tables whose key check has nothing to check, as one finding (never "Not evaluated is not a pass"). */
function notApplicableFinding(key: InapplicableKey, tables: readonly string[]): Finding {
  const sorted = [...new Set(tables)].sort();
  const n = sorted.length;
  const subject = NA_SUBJECT[key];
  const named = `${sorted.slice(0, 5).join(", ")}${n > 5 ? ` and ${n - 5} more` : ""}`;
  return {
    key: `not_applicable|${key}`,
    severity: "not_evaluated",
    level: "table",
    family: "integrity",
    metricId: key === "pk" ? PK_METRIC : "table.identity_duplicate_rate",
    text: `${n} ${n === 1 ? "table" : "tables"} ${n === 1 ? subject.one : subject.many} (${named}): the ${subject.check} is not applicable — nothing to check, not a gap. Integrity is scored over the units ${n === 1 ? "it has" : "they have"}.`,
    downgradedFrom: null,
    adjusted: null,
    notApplicable: { key, tables: sorted },
    target: { tab: "overview", table: n === 1 ? sorted[0] : undefined },
  };
}

/**
 * Where a finding ranks. ADR 0038: an adjusted key is neither the FAIL the evaluator stored nor
 * an ordinary WARN or INFO, so its findings sit after the real FAILs and before the documented
 * INFO — a copy that missed directly after the FAILs it replaced, a faithful one just before
 * the documented edges. A question keeps the evaluator's own rank. Key checks with no key
 * declared rank last (ADR 0042).
 */
function findingRank(finding: Finding): number {
  if (finding.notApplicable) return 7;
  if (finding.adjusted?.kind !== "adjusted") return statusRank(finding.severity);
  return finding.severity === "warn" ? 0.5 : 2.75;
}

/**
 * Ranked findings: FAIL, adjusted keys that missed their copy, WARN, NOT EVALUATED, adjusted keys
 * that are faithful, then documented edges (INFO). `adjustments` is the generator's half
 * (ADR 0038, the adjustments route): without it every row reads as the evaluator stored it.
 */
export function findings(
  detail: Pick<EvaluationDetail, "evaluation" | "metrics">,
  adjustments?: readonly TableModelAdjustment[],
  tolerance?: number,
): Finding[] {
  const orphanSource = new Map<string, number | null>();
  for (const row of detail.metrics)
    if (row.metric_id === "relationship.orphan_rate_source" && row.edge) orphanSource.set(row.edge, row.value);
  const out: Finding[] = [];
  const pkTables = new Set<string>();
  const inapplicable = new Map<InapplicableKey, string[]>();
  for (const row of detail.metrics) {
    if (isRollup(row.metric_id)) continue;
    const na = inapplicableKey(row);
    if (na) {
      if (row.metric_id === PK_METRIC) pkTables.add(row.table_name);
      inapplicable.set(na, [...(inapplicable.get(na) ?? []), row.table_name]);
      continue;
    }
    const reading = readingOf(row);
    // ADR 0038: the generator's verdict on this table's declared key, layered on the row.
    const adjusted =
      row.metric_id === PK_METRIC
        ? readAdjustedPk(row, adjustmentFor(row.table_name, adjustments), {
            modelAdjusted: detail.evaluation.model_adjusted,
            tolerance,
          })
        : null;
    if (row.metric_id === PK_METRIC) pkTables.add(row.table_name);
    const notable =
      row.status === "fail" ||
      row.status === "warn" ||
      row.status === "not_evaluated" ||
      (row.metric_id === "relationship.orphan_rate" && reading.documented) ||
      reading.downgradedFrom !== null ||
      statusRank(row.status) === 2.5 ||
      // A copy that missed is a finding even where the evaluator's own rule passed.
      adjusted?.kind === "adjusted";
    if (!notable) continue;
    const tab = adjusted ? "relational" : tabFor(row);
    const own = interpretRow(row, referenceNFor(detail, row.table_name), row.edge ? orphanSource.get(row.edge) : null);
    out.push({
      key: metricKey(row),
      // An adjusted reading sets the severity; a question or a disagreement keeps the evaluator's.
      severity: adjusted?.kind === "adjusted" ? adjusted.status : row.status,
      level: row.level,
      family: row.family,
      metricId: row.metric_id,
      text: !adjusted ? own : adjusted.kind === "adjusted" ? adjusted.sentence : `${own} ${adjusted.sentence}`,
      downgradedFrom: reading.downgradedFrom,
      adjusted,
      target: {
        tab,
        table: row.level === "model" ? undefined : row.table_name,
        column: tab === "columns" && row.column_name ? `${row.table_name}.${row.column_name}` : undefined,
      },
    });
  }
  // An adjusted table the evaluator wrote no PK row for still has the generator's verdict.
  for (const adjustment of adjustments ?? []) {
    if (pkTables.has(adjustment.table)) continue;
    const adjusted = readAdjustedPk(null, adjustment, {
      modelAdjusted: detail.evaluation.model_adjusted,
      tolerance,
    });
    if (adjusted?.kind !== "adjusted") continue;
    out.push({
      key: [PK_METRIC, adjustment.table, "", "", ""].join("|"),
      severity: adjusted.status,
      level: "table",
      family: "integrity",
      metricId: PK_METRIC,
      text: adjusted.sentence,
      downgradedFrom: null,
      adjusted,
      target: { tab: "relational", table: adjustment.table },
    });
  }
  for (const [key, tables] of inapplicable) out.push(notApplicableFinding(key, tables));
  const levelOrder = ["row", "table", "relationship", "field", "column", "pair", "model"];
  const familyOrder = ["privacy", "integrity", "fidelity", "diversity"];
  const familyRank = (f: string) => (familyOrder.includes(f) ? familyOrder.indexOf(f) : familyOrder.length);
  return out.sort(
    (a, b) =>
      findingRank(a) - findingRank(b) ||
      familyRank(a.family) - familyRank(b.family) ||
      levelOrder.indexOf(a.level) - levelOrder.indexOf(b.level) ||
      a.key.localeCompare(b.key),
  );
}

/**
 * The run in two or three sentences: the verdict, where the problems concentrate, what did not run.
 * `now` (epoch milliseconds; the clock by default) tells a running evaluation from a stale one.
 */
export function headline(detail: Pick<EvaluationDetail, "evaluation" | "metrics">, now: number = Date.now()): string {
  const { evaluation, metrics } = detail;
  if (!metrics.length) {
    if (isStale(evaluation, now))
      return `The evaluation is stale: its last registry event is RUNNING, recorded more than ${STALE_AFTER_HOURS} hours ago, so it most likely ended without recording a result. No metrics have been written.`;
    if (evaluation.status === "RUNNING") return "The evaluation is still running: no metrics have been written yet.";
    return `No metrics were written${evaluation.status_reason ? `: ${evaluation.status_reason}` : "."}`;
  }
  const measured = metrics.filter((m) => !isRollup(m.metric_id));
  const counts = countStatuses(measured);
  const overall = metrics.find((m) => m.metric_id === "model.overall_score")?.value ?? evaluation.overall_score;
  // ADR 0042: the not-evaluated rows that are key checks with no key declared are named apart.
  const notApplicable = measured.filter((m) => inapplicableKey(m) !== null).length;
  const parts = [
    `Overall score ${fmtScore(overall)} over ${countsTotal(counts).toLocaleString("en-US")} measured metrics: ${countsPhrase(counts, ", ", notApplicable)}.`,
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
  const downgraded = measured.filter((m) => readingOf(m).downgradedFrom !== null).length;
  if (downgraded)
    parts.push(
      `${downgraded} ${downgraded === 1 ? "pass is" : "passes are"} ≈ within noise: ${downgraded === 1 ? "a crossing" : "crossings"} that sampling noise explains at this n.`,
    );
  const privacyNotEvaluated = measured.filter((m) => m.family === "privacy" && m.status === "not_evaluated").length;
  if (privacyNotEvaluated)
    parts.push(`${privacyNotEvaluated} privacy metrics did not run — privacy is unproven here, not passed.`);
  return parts.join(" ");
}
