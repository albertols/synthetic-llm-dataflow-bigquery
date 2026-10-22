/**
 * Per-table facts of a run (ADR 0036, ADR 0044, ADR 0045): whether a table declares a primary key, its
 * foreign-key degree, a source override, its batch job, its score and status. The generation record
 * (ADR 0044) says what the launch declared; for runs written before it, the evaluator's rows and the
 * registry's run ids stand in. Pure functions (memoise them in components).
 */
import { inapplicableKey } from "@contracts/applicability";
import type { EvaluationDetail, MetricRow } from "@contracts/api";
import type { DeclaredTable, GenerationRecord } from "@contracts/record";

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
