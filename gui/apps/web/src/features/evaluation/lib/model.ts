/**
 * View models over one evaluation's metric rows: statuses ranked, columns
 * summarised for the heatmap, family scorecards, level counts. Pure
 * functions (memoise them in components); every vocabulary switch has a
 * default branch because live rows may carry values newer than the contract.
 */
import type { EvaluationDetail, EvaluationRecord, MetricRow } from "@contracts/api";

import {
  catalogueIndex,
  FAMILIES,
  isRollup,
  LEVEL_ORDER_LIST,
  metricMeta,
  modelScoreId,
  tableScoreId,
} from "./catalogue";

export const STATUSES = ["fail", "warn", "not_evaluated", "info", "pass"] as const;
const RANK: Record<string, number> = { fail: 0, warn: 1, not_evaluated: 2, info: 3, pass: 4 };

/** Lower = worse. Unknown statuses sit between not_evaluated and info. */
export function statusRank(status: string | null | undefined): number {
  return status ? (RANK[status] ?? 2.5) : 5;
}

export function worstStatus(statuses: Iterable<string>): string | null {
  let worst: string | null = null;
  for (const status of statuses) if (worst === null || statusRank(status) < statusRank(worst)) worst = status;
  return worst;
}

export type StatusCounts = Record<"pass" | "warn" | "fail" | "info" | "not_evaluated" | "other", number>;

export function emptyCounts(): StatusCounts {
  return { pass: 0, warn: 0, fail: 0, info: 0, not_evaluated: 0, other: 0 };
}

export function countStatuses(rows: Iterable<Pick<MetricRow, "status">>): StatusCounts {
  const counts = emptyCounts();
  for (const row of rows) {
    const status: string = row.status;
    if (status !== "other" && status in counts) counts[status as keyof StatusCounts] += 1;
    else counts.other += 1;
  }
  return counts;
}

/** "<metric>|<table>|<column>|<column_2>|<edge>" — the same key the compare route uses. */
export function metricKey(row: Pick<MetricRow, "metric_id" | "table_name" | "column_name" | "column_name_2" | "edge">) {
  return [row.metric_id, row.table_name, row.column_name ?? "", row.column_name_2 ?? "", row.edge ?? ""].join("|");
}

export function columnKey(table: string, column: string): string {
  return `${table}.${column}`;
}

/** Splits "table.column" on the first dot that leaves a known table (table names never hold dots here). */
export function parseColumnKey(key: string, tables: readonly string[]): { table: string; column: string } | null {
  for (const table of tables) {
    if (key.startsWith(`${table}.`) && key.length > table.length + 1) {
      return { table, column: key.slice(table.length + 1) };
    }
  }
  const dot = key.indexOf(".");
  if (dot <= 0 || dot === key.length - 1) return null;
  return { table: key.slice(0, dot), column: key.slice(dot + 1) };
}

// ---------------------------------------------------------------- columns --

export interface ColumnSummary {
  key: string;
  table: string;
  column: string;
  kind: string | null;
  metrics: MetricRow[];
  byMetric: Map<string, MetricRow>;
  counts: StatusCounts;
  worst: string | null;
  /** Mean score of the column's scored metrics (the roll-up unit). */
  meanScore: number | null;
}

/** Field- and column-level rows grouped per column, worst first (fails, then warns, then name). */
export function columnSummaries(metrics: readonly MetricRow[]): ColumnSummary[] {
  const groups = new Map<string, ColumnSummary>();
  for (const row of metrics) {
    if ((row.level !== "field" && row.level !== "column") || !row.column_name) continue;
    const key = columnKey(row.table_name, row.column_name);
    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        table: row.table_name,
        column: row.column_name,
        kind: row.column_kind,
        metrics: [],
        byMetric: new Map(),
        counts: emptyCounts(),
        worst: null,
        meanScore: null,
      };
      groups.set(key, group);
    }
    group.metrics.push(row);
    group.byMetric.set(row.metric_id, row);
    group.kind ??= row.column_kind;
  }
  for (const group of groups.values()) {
    group.counts = countStatuses(group.metrics);
    // INFO rows (no thresholds) never make a column look worse than its gated metrics.
    group.worst =
      worstStatus(group.metrics.filter((m) => m.status !== "info").map((m) => m.status)) ??
      worstStatus(group.metrics.map((m) => m.status));
    const scores = group.metrics.map((m) => m.score).filter((s): s is number => s !== null && Number.isFinite(s));
    group.meanScore = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
  }
  return [...groups.values()].sort(
    (a, b) =>
      statusRank(a.worst) - statusRank(b.worst) ||
      b.counts.fail - a.counts.fail ||
      b.counts.warn - a.counts.warn ||
      a.key.localeCompare(b.key),
  );
}

export interface HeatmapOptions {
  table?: string;
  family?: string;
  problemsOnly?: boolean;
  query?: string;
}

export interface HeatmapModel {
  metricIds: string[];
  rows: ColumnSummary[];
  /** Columns before filtering. */
  total: number;
}

/** The rows and metric columns of the column × metric heatmap under the current filters. */
export function heatmapModel(summaries: readonly ColumnSummary[], options: HeatmapOptions = {}): HeatmapModel {
  const query = options.query?.trim().toLowerCase();
  const family = options.family && options.family !== "all" ? options.family : undefined;
  const rows: ColumnSummary[] = [];
  const ids = new Set<string>();
  for (const summary of summaries) {
    if (options.table && summary.table !== options.table) continue;
    if (query && !summary.key.toLowerCase().includes(query)) continue;
    const metrics = family ? summary.metrics.filter((m) => m.family === family) : summary.metrics;
    if (!metrics.length) continue;
    const worst = worstStatus(metrics.map((m) => m.status));
    if (options.problemsOnly && !(worst === "fail" || worst === "warn" || worst === "not_evaluated")) continue;
    rows.push(summary);
    for (const m of metrics) ids.add(m.metric_id);
  }
  const levelOf = (id: string) => (id.startsWith("field.") ? 0 : 1);
  const metricIds = [...ids].sort(
    (a, b) => levelOf(a) - levelOf(b) || catalogueIndex(a) - catalogueIndex(b) || a.localeCompare(b),
  );
  return { metricIds, rows, total: summaries.length };
}

// ----------------------------------------------------------- scorecards --

export interface LevelCount {
  level: string;
  counts: StatusCounts;
  total: number;
}

/** Status counts per catalogue level (roll-ups excluded), in catalogue level order. */
export function levelCounts(metrics: readonly MetricRow[]): LevelCount[] {
  const map = new Map<string, MetricRow[]>();
  for (const row of metrics) {
    if (isRollup(row.metric_id)) continue;
    const list = map.get(row.level) ?? [];
    list.push(row);
    map.set(row.level, list);
  }
  const order = (level: string) => {
    const index = (LEVEL_ORDER_LIST as readonly string[]).indexOf(level);
    return index < 0 ? LEVEL_ORDER_LIST.length : index;
  };
  return [...map.entries()]
    .sort(([a], [b]) => order(a) - order(b))
    .map(([level, rows]) => ({ level, counts: countStatuses(rows), total: rows.length }));
}

export interface FamilyCard {
  family: string;
  /** The model roll-up row, when the evaluator wrote one. */
  rollup: MetricRow | null;
  score: number | null;
  status: string | null;
  tables: { table: string; row: MetricRow | null; score: number | null; status: string | null }[];
  levels: LevelCount[];
  /** The measured (non-roll-up) rows of the family. */
  metrics: MetricRow[];
  /** Up to `worstN` rows to look at first: fails, warns, not evaluated, then the lowest scores. */
  worst: MetricRow[];
}

const REGISTRY_SCORE: Record<string, keyof EvaluationRecord> = {
  overall: "overall_score",
  fidelity: "fidelity_score",
  privacy: "privacy_score",
  integrity: "integrity_score",
  diversity: "diversity_score",
};

export function tableNames(evaluation: Pick<EvaluationRecord, "tables">, metrics: readonly MetricRow[]): string[] {
  const names = new Set<string>();
  for (const t of evaluation.tables) if (t.name) names.add(t.name);
  for (const m of metrics) if (m.level !== "model") names.add(m.table_name);
  return [...names];
}

/**
 * The score used to rank passing rows. Catalogue `score: none` metrics that are not roll-ups
 * (distinct_ceiling_hit, source_stats_drift, wasserstein …) store score = value, which is not a
 * quality score (0 = best for a ceiling hit), so they never rank as "worst".
 */
function rankScore(row: MetricRow): number {
  if (row.score === null) return 2;
  return metricMeta(row.metric_id)?.score === "none" ? 2 : row.score;
}

/** "Look first" order: fail, warn, not evaluated, unknown, then passes by lowest score; INFO rows last. */
function lookRank(status: string): number {
  return status === "info" ? 6 : statusRank(status);
}

/** The `n` rows to look at first, one per metric id (three Pearson Δ rows teach less than three different metrics). */
export function worstRows(rows: readonly MetricRow[], n: number): MetricRow[] {
  const sorted = [...rows]
    .filter((row) => !isRollup(row.metric_id))
    .sort(
      (a, b) =>
        lookRank(a.status) - lookRank(b.status) ||
        rankScore(a) - rankScore(b) ||
        metricKey(a).localeCompare(metricKey(b)),
    );
  const seen = new Set<string>();
  const out: MetricRow[] = [];
  for (const row of sorted) {
    if (seen.has(row.metric_id)) continue;
    seen.add(row.metric_id);
    out.push(row);
    if (out.length === n) break;
  }
  return out;
}

/** Overall + one card per family, from the model roll-ups (falling back to the registry's scores). */
export function familyCards(detail: Pick<EvaluationDetail, "evaluation" | "metrics">, worstN = 3): FamilyCard[] {
  const { evaluation, metrics } = detail;
  const tables = tableNames(evaluation, metrics).filter((name) =>
    metrics.some((m) => m.table_name === name && m.level !== "model"),
  );
  return ["overall", ...FAMILIES].map((family) => {
    const rollup = metrics.find((m) => m.metric_id === modelScoreId(family)) ?? null;
    const registryKey = REGISTRY_SCORE[family];
    const registryScore = registryKey ? (evaluation[registryKey] as number | null) : null;
    const scoped = family === "overall" ? metrics : metrics.filter((m) => m.family === family);
    const measured = scoped.filter((m) => !isRollup(m.metric_id));
    return {
      family,
      rollup,
      score: rollup?.value ?? registryScore ?? null,
      status: rollup?.status ?? null,
      tables: tables.map((table) => {
        const row = metrics.find((m) => m.metric_id === tableScoreId(family) && m.table_name === table) ?? null;
        return { table, row, score: row?.value ?? null, status: row?.status ?? null };
      }),
      levels: levelCounts(measured),
      metrics: measured,
      worst: worstRows(measured, worstN),
    };
  });
}

/** The overall score per table (table.overall_score, else the registry's tables[].table_score). */
export function tableScores(detail: Pick<EvaluationDetail, "evaluation" | "metrics">): Map<string, MetricRow | null> {
  const out = new Map<string, MetricRow | null>();
  for (const row of detail.metrics) if (row.metric_id === "table.overall_score") out.set(row.table_name, row);
  return out;
}

/** Pair-level rows, optionally for one table, worst first then largest value. */
export function pairRows(metrics: readonly MetricRow[], table?: string): MetricRow[] {
  return metrics
    .filter((m) => m.level === "pair" && (!table || m.table_name === table))
    .sort((a, b) => statusRank(a.status) - statusRank(b.status) || (b.value ?? -1) - (a.value ?? -1));
}

export const LIFT_METRICS = [
  "row.memorization_lift",
  "row.exposure_lift",
  "row.near_match_lift",
  "field.value_memorization_lift",
  "field.pool_memorization_lift",
] as const;

export const PRIVACY_RATE_METRICS = [
  "row.exact_match_rate",
  "row.exact_match_rate_nonkey",
  "row.near_match_rate",
  "field.substantive_copy_rate",
] as const;
