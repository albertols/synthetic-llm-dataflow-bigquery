/**
 * A table's integrity units — the evaluator's roll-up units (`scoring._unit`, Ruling R11): each
 * column once (its field and column rows), the pair group once, each row- and table-level metric
 * once, each foreign key where the table is the child once. `table.integrity_score` is the mean over
 * the units with a finite score; a unit with none (a key check with no key declared, an INFO row) is
 * left out of it. The roll-up row carries no unit list, so the GUI rebuilds it from the metric rows,
 * keyed by the same `unitOf` the stats package ports from the evaluator (golden-pinned, ADR 0042).
 */
import { inapplicableKey, NOT_APPLICABLE_LABEL } from "@contracts/applicability";
import type { MetricRow } from "@contracts/api";
import { unitOf } from "@synthetic-platform/stats/scoring";

import { isRollup, metricShort } from "./catalogue";
import { reasonOf } from "./reading";

export type UnitState = "counted" | "not_applicable" | "not_scored";

export interface IntegrityUnit {
  key: string;
  label: string;
  state: UnitState;
  /** The mean of the unit's finite scores; null when it has none. */
  score: number | null;
  /** Why a unit did not count. */
  note: string | null;
}

function labelOf(row: MetricRow): string {
  switch (row.level) {
    case "field":
    case "column":
      return row.column_name ?? "column";
    case "pair":
      return "column pairs";
    case "relationship":
      return row.edge ?? "edge";
    default:
      return metricShort(row.metric_id);
  }
}

const ORDER: Record<UnitState, number> = { counted: 0, not_applicable: 1, not_scored: 2 };

/** A row that is one of a table's integrity units' rows: integrity family, below the model, not a roll-up. */
const isUnitRow = (row: MetricRow) => row.family === "integrity" && row.level !== "model" && !isRollup(row.metric_id);

/** One table's integrity units. */
export function integrityUnits(metrics: readonly MetricRow[], table: string): IntegrityUnit[] {
  return unitsOf(metrics.filter((row) => row.table_name === table && isUnitRow(row)));
}

/**
 * Every table's integrity units, from one pass over the metric rows: a scorecard groups them once, never
 * once per visible row per scroll (a corp-width table has 10–25 times the mock's rows, ADR 0036).
 */
export function integrityUnitsByTable(metrics: readonly MetricRow[]): Map<string, IntegrityUnit[]> {
  const rowsByTable = new Map<string, MetricRow[]>();
  for (const row of metrics) {
    if (!isUnitRow(row)) continue;
    const rows = rowsByTable.get(row.table_name);
    if (rows) rows.push(row);
    else rowsByTable.set(row.table_name, [row]);
  }
  return new Map([...rowsByTable].map(([table, rows]) => [table, unitsOf(rows)]));
}

/** The units of one table's integrity rows, keyed as the evaluator keys them (`unitOf`). */
function unitsOf(tableRows: readonly MetricRow[]): IntegrityUnit[] {
  const groups = new Map<string, { label: string; rows: MetricRow[] }>();
  for (const row of tableRows) {
    const key = unitOf(row);
    const group = groups.get(key) ?? { label: labelOf(row), rows: [] };
    group.rows.push(row);
    groups.set(key, group);
  }
  const units: IntegrityUnit[] = [];
  for (const [key, { label, rows }] of groups) {
    const scores = rows.map((r) => r.score).filter((s): s is number => s !== null && Number.isFinite(s));
    if (scores.length) {
      units.push({
        key,
        label,
        state: "counted",
        score: scores.reduce((a, b) => a + b, 0) / scores.length,
        note: null,
      });
      continue;
    }
    const keys = rows.map((r) => inapplicableKey(r));
    if (keys.every((k) => k !== null)) {
      units.push({ key, label, state: "not_applicable", score: null, note: NOT_APPLICABLE_LABEL[keys[0]!] });
      continue;
    }
    const reason = rows.map((r) => reasonOf(r)).find((r) => r !== null) ?? null;
    const note = reason ?? (rows.every((r) => r.status === "info") ? "informational: no score" : "no score recorded");
    units.push({ key, label, state: "not_scored", score: null, note });
  }
  return units.sort((a, b) => ORDER[a.state] - ORDER[b.state] || a.label.localeCompare(b.label));
}

/** The mean over the counted units (what `table.integrity_score` stores). */
export function unitsMean(units: readonly IntegrityUnit[]): number | null {
  const counted = units.filter((u) => u.state === "counted").map((u) => u.score!);
  return counted.length ? counted.reduce((a, b) => a + b, 0) / counted.length : null;
}
