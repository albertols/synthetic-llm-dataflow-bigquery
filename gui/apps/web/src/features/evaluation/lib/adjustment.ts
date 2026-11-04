/**
 * The adjusted reading of `table.pk_duplicate_rate` (ADR 0038).
 *
 * Two measures describe the same declared key. The evaluator's duplicate rate
 * counts every row whose key occurs twice or more, first rows included, and
 * fails on any value above 0. The generator's repeat share counts the rows
 * beyond the first per key (1 − distinct keys ÷ rows) and, on a table whose
 * declared key the source disproved, compares it with the source's share.
 * Both are right; they answer different questions.
 *
 * This module layers the generator's verdict on top of the evaluator's row —
 * exactly as the documented-edge rule (`isDocumentedEdge`, Ruling R42) and the
 * noise downgrade (Ruling R40) do: the row, its status and the status counts
 * are never changed, and the raw evaluator status rides along in every reading.
 */
import type { TableModelAdjustment, AdjustmentVerdict } from "@contracts/adjustments";
import type { EvaluationDetail, MetricRow } from "@contracts/api";

import {
  fmtPoints,
  fmtRepeatShare,
  fmtTolerance,
  REPEAT_SHARE_TOLERANCE,
  VERDICT_WORD,
  verdictMark,
} from "@/lib/adjustments";

import { fmtCompared } from "./format";
import { detailOf, reasonOf, statusWord } from "./reading";

export const PK_METRIC = "table.pk_duplicate_rate";

/** The evaluator's and the generator's shares are the same count over the same rows: equal to rounding. */
export const CROSS_CHECK_TOLERANCE = 1e-6;

export function adjustmentFor(
  table: string,
  adjustments: readonly TableModelAdjustment[] | undefined,
): TableModelAdjustment | undefined {
  return adjustments?.find((a) => a.table === table);
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * The generator's measure from the evaluator's own counts: 1 − detail.distinct_keys ÷ rows, the
 * share of rows beyond the first per key (ADR 0038 D4). `rowsSynthetic` defaults to the row's
 * n_synthetic, the denominator of its own rate. Null when the row carries no counts.
 */
export function repeatShareFromEvaluator(
  row: Pick<MetricRow, "detail" | "n_synthetic"> | null | undefined,
  rowsSynthetic?: number | null,
): number | null {
  if (!row) return null;
  const distinct = count(detailOf(row).distinct_keys);
  const rows = rowsSynthetic === undefined ? row.n_synthetic : rowsSynthetic;
  if (distinct === null || rows === null || rows <= 0) return null;
  return 1 - distinct / rows;
}

/** ADR 0038 cross-check: the two shares agree within 1e-6; null when either side is missing. */
export function measuresAgree(evaluatorShare: number | null, landingShare: number | null): boolean | null {
  if (evaluatorShare === null || landingShare === null) return null;
  return Math.abs(evaluatorShare - landingShare) <= CROSS_CHECK_TOLERANCE;
}

export interface AdjustedReading {
  /** `adjusted`: the Adjusted pill replaces the evaluator's; `question`: an "Adjusted?" badge next to it; `disagree`: a warning instead of a verdict. */
  kind: "adjusted" | "question" | "disagree";
  verdict: AdjustmentVerdict;
  /** The finding's severity: info or warn for an adjusted reading, else the evaluator's own status. */
  status: string;
  tone: "info" | "warn" | null;
  label: string;
  /** The verdict in a sentence (adjusted), or what to append to the evaluator's own sentence. */
  sentence: string;
  /** The evaluator's stored status, kept in every reading; null when it wrote no PK row for the table. */
  evaluatorStatus: string | null;
  /** "evaluator: FAIL (threshold 0)" — what stays visible wherever the Adjusted pill replaces its pill. */
  evaluatorText: string | null;
  /** 1 − distinct keys ÷ rows, from the evaluator's counts. */
  evaluatorShare: number | null;
  /** Whether that share equals the generator's landing share; null when it cannot be checked. */
  agrees: boolean | null;
}

function evaluatorTextOf(row: MetricRow | null): string | null {
  if (!row) return null;
  const zero = row.threshold_warn === 0 && row.threshold_fail === 0;
  return `evaluator: ${statusWord(row.status)}${zero && row.status !== "not_evaluated" ? " (threshold 0)" : ""}`;
}

/** What the evaluator's own number says, as the last sentence of an adjusted reading. */
function evaluatorClause(row: MetricRow | null): string {
  if (!row) return "";
  if (row.status === "not_evaluated")
    return ` The evaluator did not grade this key: ${reasonOf(row) ?? "no reason recorded"}.`;
  if (row.value === null) return "";
  const value = fmtCompared([row.value, 0], "share")[0]!;
  return row.value > 0
    ? ` The evaluator's ${value} counts every row on a repeated key, first rows included.`
    : ` The evaluator's ${value} passes its own rule, which expects a unique key.`;
}

function adjustedSentence(a: TableModelAdjustment, row: MetricRow | null, toleranceShare: number): string {
  const source = fmtRepeatShare(a.source_repeat_share);
  const synthetic = fmtRepeatShare(a.landing_repeat_share);
  const delta = fmtPoints(a.repeat_share_delta);
  const tolerance = fmtTolerance(toleranceShare);
  const tail = evaluatorClause(row);
  if (a.verdict === "faithful")
    return `${a.table}'s declared key repeats in the source (${source} of rows), so the launch adjusted the model (ADR 0038) and copied that share: synthetic ${synthetic} (Δ ${delta}, within ${tolerance}).${tail}`;
  if (a.verdict === "over")
    return `${a.table}'s declared key repeats in ${source} of source rows but ${synthetic} of synthetic rows (Δ ${delta}, outside ${tolerance}): the synthetic table repeats its key more than its source.${tail}`;
  if (a.landing_repeat_share === 0)
    return `${a.table}'s declared key repeats in ${source} of source rows but in none of the synthetic rows (Δ ${delta}, outside ${tolerance}): the synthetic table is fully unique where its source is not.${tail}`;
  return `${a.table}'s declared key repeats in ${source} of source rows but only ${synthetic} of synthetic rows (Δ ${delta}, outside ${tolerance}): the synthetic table is more unique than its source.${tail}`;
}

/**
 * The generator's reading of one table's declared key, layered on the evaluator's PK row (which
 * may be missing). Null when there is nothing to add: the table was not adjusted, the row is not
 * the PK duplicate rate, no adjustment arrived, or the join failed on a launch the registry says
 * adjusted nothing (`modelAdjusted: false`).
 */
export function readAdjustedPk(
  row: MetricRow | null | undefined,
  adjustment: TableModelAdjustment | undefined,
  options: { modelAdjusted?: boolean | null; tolerance?: number } = {},
): AdjustedReading | null {
  if (!adjustment) return null;
  const pk = row ?? null;
  if (pk && pk.metric_id !== PK_METRIC) return null;
  const mark = verdictMark(adjustment.verdict);
  if (!mark) return null;
  if (adjustment.verdict === "unknown" && options.modelAdjusted === false) return null;
  const evaluatorShare = repeatShareFromEvaluator(pk);
  const base = {
    verdict: adjustment.verdict,
    evaluatorStatus: pk?.status ?? null,
    evaluatorText: evaluatorTextOf(pk),
    evaluatorShare,
  };
  if (mark.kind === "question") {
    const sentence =
      adjustment.verdict === "unmeasured"
        ? `The launch adjusted this key (ADR 0038: pk.duplicate is left out of ${adjustment.table}'s gate) but measured no source repeat share, so whether these repeats copy the source is not known.`
        : `No validation_runs row could be joined to ${adjustment.table}, so whether the launch adjusted this key (ADR 0038) is not known: read the launch log's model_adjustment milestones.`;
    return {
      ...base,
      kind: "question",
      status: pk?.status ?? "not_evaluated",
      tone: null,
      label: mark.label,
      sentence,
      agrees: null,
    };
  }
  const agrees = measuresAgree(evaluatorShare, adjustment.landing_repeat_share);
  if (agrees === false) {
    // ADR 0038: the two shares count the same thing; when they differ, neither verdict is shown.
    const nulls = count(detailOf(pk!).null_key_rows) ?? 0;
    const why = nulls
      ? `${nulls.toLocaleString("en-US")} rows have a NULL key part: the evaluator leaves them out of the keys, the generator groups NULL as a value.`
      : "Check that the evaluation read the rows this run landed (its scope).";
    return {
      ...base,
      kind: "disagree",
      status: pk!.status,
      tone: "warn",
      label: "Measures disagree",
      sentence: `Measures disagree on ${adjustment.table}: the evaluator's counts give a synthetic repeat share of ${fmtRepeatShare(evaluatorShare)} (1 − distinct keys ÷ rows) where the generator recorded ${fmtRepeatShare(adjustment.landing_repeat_share)}, so no adjusted reading is shown. ${why}`,
      agrees,
    };
  }
  return {
    ...base,
    kind: "adjusted",
    status: mark.tone,
    tone: mark.tone,
    label: mark.label,
    sentence: adjustedSentence(adjustment, pk, options.tolerance ?? REPEAT_SHARE_TOLERANCE),
    agrees,
  };
}

// ------------------------------------------------------------ view models --

/** One table of the launch: the generator's row, the evaluator's PK row, and the reading of both. */
export interface AdjustmentRow {
  table: string;
  adjustment: TableModelAdjustment;
  /** The evaluator's `table.pk_duplicate_rate` row, untouched; null when it wrote none. */
  row: MetricRow | null;
  reading: AdjustedReading | null;
  /** The declared key (`detail.key_columns`). */
  keyColumns: string[];
  evaluatorShare: number | null;
}

/** Every table the adjustments route returned, in its order, with the evaluator's PK row alongside. */
export function adjustmentRows(
  detail: Pick<EvaluationDetail, "evaluation" | "metrics">,
  adjustments: readonly TableModelAdjustment[] | undefined,
  tolerance: number = REPEAT_SHARE_TOLERANCE,
): AdjustmentRow[] {
  const pk = new Map<string, MetricRow>();
  for (const row of detail.metrics) if (row.metric_id === PK_METRIC) pk.set(row.table_name, row);
  return (adjustments ?? []).map((adjustment) => {
    const row = pk.get(adjustment.table) ?? null;
    const columns = row ? detailOf(row).key_columns : null;
    return {
      table: adjustment.table,
      adjustment,
      row,
      reading: readAdjustedPk(row, adjustment, { modelAdjusted: detail.evaluation.model_adjusted, tolerance }),
      keyColumns: Array.isArray(columns) ? columns.filter((c): c is string => typeof c === "string") : [],
      evaluatorShare: repeatShareFromEvaluator(row),
    };
  });
}

/** Tables `validation_runs` says the launch adjusted (`pk.duplicate` left out of their gate). */
export function adjustedTableCount(adjustments: readonly TableModelAdjustment[] | undefined): number {
  return (adjustments ?? []).filter((a) => a.adjusted === true).length;
}

const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/**
 * The line under the headline: how many of the evaluator's PK-duplicate FAILs sit on keys the
 * launch adjusted, and how the generator read them. The evaluator's counts above it are untouched.
 */
export function adjustedSummary(rows: readonly AdjustmentRow[]): string | null {
  const adjusted = rows.filter((r) => r.reading?.kind === "adjusted");
  const failing = adjusted.filter((r) => r.reading!.evaluatorStatus === "fail");
  const parts: string[] = [];
  if (failing.length) {
    const byVerdict = (["faithful", "under", "over"] as const)
      .map((v) => [v, failing.filter((r) => r.adjustment.verdict === v).length] as const)
      .filter(([, n]) => n > 0)
      .map(([v, n]) => `${n} ${VERDICT_WORD[v]}`)
      .join(", ");
    parts.push(
      `${plural(failing.length, "PK-duplicate FAIL is on a key", "PK-duplicate FAILs are on keys")} the launch adjusted (ADR 0038) — ${byVerdict}.`,
    );
  }
  // A copy that missed while the evaluator's own rule passes: nothing above would show it.
  for (const verdict of ["under", "over"] as const) {
    const quiet = adjusted.filter((r) => r.reading!.evaluatorStatus !== "fail" && r.adjustment.verdict === verdict);
    if (quiet.length)
      parts.push(
        `${plural(quiet.length, "more adjusted key passes", "more adjusted keys pass")} the evaluator's rule and ${quiet.length === 1 ? "is" : "are"} ${VERDICT_WORD[verdict]}.`,
      );
  }
  const open = rows.filter((r) => r.reading?.kind === "question" && r.reading.evaluatorStatus === "fail");
  if (open.length)
    parts.push(
      `${plural(open.length, "more PK-duplicate FAIL is on a key", "more PK-duplicate FAILs are on keys")} whose adjustment could not be read (Adjusted?).`,
    );
  const disagree = rows.filter((r) => r.reading?.kind === "disagree");
  if (disagree.length)
    parts.push(
      `On ${plural(disagree.length, "table", "tables")} the two measures disagree: no adjusted reading there.`,
    );
  return parts.length ? parts.join(" ") : null;
}
