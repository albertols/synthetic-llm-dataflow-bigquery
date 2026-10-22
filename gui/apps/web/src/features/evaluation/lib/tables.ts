/**
 * Per-table facts of a run (ADR 0036, ADR 0044, ADR 0045): whether a table declares a primary key, its
 * foreign-key degree, a source override, its batch job, its score and status. The generation record
 * (ADR 0044) says what the launch declared; for runs written before it, the evaluator's rows and the
 * registry's run ids stand in. Pure functions (memoise them in components).
 */
import { inapplicableKey } from "@contracts/applicability";
import type { EvaluationDetail, EvaluationRecord, MetricRow } from "@contracts/api";
import type { DeclaredTable, GenerationRecord, RecordBatch } from "@contracts/record";
import type { RelationshipModel } from "@contracts/relational";

import { ZERO_TOLERANCE_IDS } from "./catalogue";
import type { Graph } from "./graph";
import { tableNames } from "./model";

export type KeyState = "declared" | "none" | "unknown";
export type TableStatus = "pass" | "warn" | "fail" | "not_evaluated";

export interface TableFacts {
  name: string;
  /** From the record's declared table, else from the evaluator's PK row. */
  pk: KeyState;
  pkColumns: string[];
  /** Foreign keys that touch the table in the drawn graph (as child or parent); 0 = isolated. */
  degree: number;
  /** null: not recorded. */
  enabled: boolean | null;
  /** The declared `source_table` (ADR 0045); null = the default source, or not recorded. */
  sourceOverride: string | null;
  /** The batch job (b00 = 0) that generated it; null = not a batched launch, or not known. */
  batch: number | null;
  score: number | null;
  status: TableStatus;
  /** FAILed zero-tolerance key checks on the table (duplicate keys, orphans). */
  keyFailures: number;
  from: "record" | "registry";
}

const PK = "table.pk_duplicate_rate";

/** "b00", "b11", "b123". */
export function batchLabel(index: number): string {
  return `b${String(index).padStart(2, "0")}`;
}

/**
 * The batch job of a run id `<base>-bNN-MM-<table>` (ADR 0045); null for any other id, including the batch
 * job's own `<base>-bNN`, which carries no table.
 */
export function batchOfRunId(runId: string | null | undefined, base: string | null | undefined): number | null {
  if (!runId || !base || !runId.startsWith(`${base}-b`)) return null;
  const match = /^b(\d{2,})-\d{2,}-.+$/.exec(runId.slice(base.length + 1));
  return match ? Number(match[1]) : null;
}

function keyColumns(row: MetricRow | undefined): string[] {
  const detail = row?.detail;
  const cols =
    detail && typeof detail === "object" && !Array.isArray(detail)
      ? (detail as { key_columns?: unknown }).key_columns
      : null;
  return Array.isArray(cols) ? cols.filter((c): c is string => typeof c === "string") : [];
}

function pkOf(declared: DeclaredTable | undefined, row: MetricRow | undefined): { pk: KeyState; pkColumns: string[] } {
  if (declared) return { pk: declared.pk.length ? "declared" : "none", pkColumns: declared.pk };
  if (row && inapplicableKey(row) === "pk") return { pk: "none", pkColumns: [] };
  if (row && row.status !== "not_evaluated") return { pk: "declared", pkColumns: keyColumns(row) };
  return { pk: "unknown", pkColumns: [] };
}

const STATUSES: ReadonlySet<string> = new Set(["pass", "warn", "fail"]);

/** One entry per table the run generated (external parents are not the launch's tables), in registry order. */
export function tableFacts(input: {
  detail: Pick<EvaluationDetail, "evaluation" | "metrics">;
  graph: Pick<Graph, "edges">;
  record?: Pick<GenerationRecord, "declared_tables" | "batches"> | null;
}): TableFacts[] {
  const { evaluation, metrics } = input.detail;
  const declared = new Map((input.record?.declared_tables ?? []).map((t) => [t.name, t]));
  const batchOfTable = new Map<string, number>();
  for (const batch of input.record?.batches ?? []) for (const name of batch.tables) batchOfTable.set(name, batch.index);
  const external = new Set(evaluation.tables.filter((t) => t.role === "external").map((t) => t.name));
  const out: TableFacts[] = [];
  for (const name of tableNames(evaluation, metrics)) {
    if (external.has(name)) continue;
    const rows = metrics.filter((m) => m.table_name === name);
    const scoreRow = rows.find((m) => m.metric_id === "table.overall_score");
    const registry = evaluation.tables.find((t) => t.name === name);
    const keyFailures = rows.filter((m) => ZERO_TOLERANCE_IDS.has(m.metric_id) && m.status === "fail").length;
    const own = declared.get(name);
    out.push({
      name,
      ...pkOf(
        own,
        rows.find((m) => m.metric_id === PK),
      ),
      degree: input.graph.edges.filter((e) => e.child === name || e.parent === name).length,
      enabled: own?.enabled ?? null,
      sourceOverride: own?.source_table ?? null,
      batch: batchOfTable.get(name) ?? batchOfRunId(registry?.run_id, evaluation.base_run_id),
      score: scoreRow?.value ?? registry?.table_score ?? null,
      status: keyFailures
        ? "fail"
        : scoreRow && STATUSES.has(scoreRow.status)
          ? (scoreRow.status as TableStatus)
          : "not_evaluated",
      keyFailures,
      from: own ? "record" : "registry",
    });
  }
  return out;
}

/** The card shows from this many tables (a one-table run has nothing to summarise). */
export const SUMMARY_MIN_TABLES = 2;

export interface ModelSummary {
  model: string | null;
  sha12: string | null;
  /** "record": the launch's generation record (ADR 0044); "registry": the registry and the metric rows. */
  source: "record" | "registry";
  /** The tables the run generated (its facts). */
  tables: number;
  /** Tables and disabled tables of the whole model: only when the GUI can read it. */
  declaredTotal: number | null;
  disabled: number | null;
  withPk: number;
  withoutPk: number;
  pkUnknown: number;
  /** Foreign keys the graph draws. */
  edges: number;
  /** Tables with no foreign key. */
  isolated: number;
  /** Tables read from another source table (ADR 0045); null without a record. */
  sourceOverrides: number | null;
  /** Batch jobs the launch planned (`batch.count`); null for a launch in one job. */
  batchCount: number | null;
  /** The batch jobs whose rows were recorded. */
  batches: readonly RecordBatch[];
}

export function modelSummary(input: {
  evaluation: Pick<EvaluationRecord, "relationship_model" | "relationship_model_sha">;
  record?: Pick<GenerationRecord, "snapshot" | "declared_tables" | "batches"> | null;
  facts: readonly TableFacts[];
  graph: Pick<Graph, "edges">;
  model?: Pick<RelationshipModel, "sha12" | "tables"> | null;
}): ModelSummary {
  const { evaluation, record, facts, graph, model } = input;
  const relationships = record?.snapshot?.relationships;
  const recordedModel = typeof relationships?.model === "string" ? relationships.model : null;
  const recordedSha = typeof relationships?.sha12 === "string" ? relationships.sha12 : null;
  const sha12 = recordedSha ?? evaluation.relationship_model_sha;
  const fromRecord = (record?.declared_tables.length ?? 0) > 0;
  // A launch records only the tables it generated: disabled ones are known only from the whole model.
  const whole = model && (sha12 === null || model.sha12 === sha12) ? model : null;
  const batches = record?.batches ?? [];
  return {
    model: recordedModel ?? evaluation.relationship_model,
    sha12,
    source: fromRecord ? "record" : "registry",
    tables: facts.length,
    declaredTotal: whole ? whole.tables.length : null,
    disabled: whole ? whole.tables.filter((t) => !t.enabled).length : null,
    withPk: facts.filter((f) => f.pk === "declared").length,
    withoutPk: facts.filter((f) => f.pk === "none").length,
    pkUnknown: facts.filter((f) => f.pk === "unknown").length,
    edges: graph.edges.length,
    isolated: facts.filter((f) => f.degree === 0).length,
    sourceOverrides: fromRecord ? facts.filter((f) => f.sourceOverride !== null).length : null,
    batchCount: batches.length ? Math.max(...batches.map((b) => b.count)) : null,
    batches,
  };
}

/**
 * The launch declared no foreign key between its tables (the zero-edge message): two or more tables,
 * no edge drawn, and a model or the record drew the graph; or, without either, every registry role is
 * `isolated` (the evaluator's tag for a table with no edge).
 */
export function declaresNoEdges(input: {
  facts: readonly TableFacts[];
  graph: Pick<Graph, "edges" | "resolvedFrom">;
  evaluation: Pick<EvaluationRecord, "tables">;
}): boolean {
  if (input.facts.length < 2 || input.graph.edges.length > 0) return false;
  if (input.graph.resolvedFrom !== "edges") return true;
  const roles = input.evaluation.tables.filter((t) => t.role !== "external").map((t) => t.role);
  return roles.length >= 2 && roles.every((r) => r === "isolated");
}

/** The filter bar shows from this many tables (ADR 0042; below it every table is already in view). */
export const FILTER_MIN_TABLES = 10;

export type TableSort = "score" | "name";

export interface TableFilter {
  /** Case-insensitive part of the table's name. */
  query?: string;
  pk?: "yes" | "no";
  status?: TableStatus;
  batch?: number;
  /** "score" (the default): worst first, failing tables then the lowest scores. */
  sort?: TableSort;
}

const STATUS_ORDER: Record<TableStatus, number> = { fail: 0, warn: 1, not_evaluated: 2, pass: 3 };

const byName = (a: TableFacts, b: TableFacts) => a.name.localeCompare(b.name);
const byWorst = (a: TableFacts, b: TableFacts) =>
  STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || (a.score ?? 2) - (b.score ?? 2) || byName(a, b);

export function filterTables(facts: readonly TableFacts[], filter: TableFilter): TableFacts[] {
  const query = filter.query?.trim().toLowerCase();
  return facts
    .filter(
      (f) =>
        (!query || f.name.toLowerCase().includes(query)) &&
        (!filter.pk || (filter.pk === "yes" ? f.pk === "declared" : f.pk === "none")) &&
        (!filter.status || f.status === filter.status) &&
        (filter.batch === undefined || f.batch === filter.batch),
    )
    .sort(filter.sort === "name" ? byName : byWorst);
}

/**
 * What the tiles and the per-table scorecards show under a filter: these tables, in this order.
 * Built from the run's facts only, so a table the evaluation does not cover (no facts, no score,
 * ADR 0036) is never in `order` and never counted in `total`.
 */
export interface TableView {
  order: readonly string[];
  visible: ReadonlySet<string>;
  /** Every table the run covers. */
  total: number;
}

export function tableView(facts: readonly TableFacts[], filter: TableFilter): TableView {
  const order = filterTables(facts, filter).map((f) => f.name);
  return { order, visible: new Set(order), total: facts.length };
}
