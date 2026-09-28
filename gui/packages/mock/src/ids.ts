/** Identifiers shared by the mock's tables: reference digests, run ids, per-table row counts. */
import { sha256Hex } from "@synthetic-platform/stats";

import type { EvalSpec } from "./storyline";
import { sourceFqn, type TableDef } from "./thelook";

/** The reference digest a launch computes: one per (table, sample size, source snapshot era). */
export function referenceDigest(table: string, referenceRowsLimit: number, tier: "sample" | "exact"): string {
  return sha256Hex(
    `${sourceFqn(table)}|${referenceRowsLimit}|${tier === "exact" ? "2026-09-snapshot" : "2026-08-snapshot"}`,
  );
}

/** The launch's root run id; each table's run id appends `-<table>`. */
export function baseRunId(spec: Pick<EvalSpec, "id" | "evaluatedAt">): string {
  const d = spec.evaluatedAt.slice(0, 16).replace(/[-:]/g, "").replace("T", "-");
  return `sdfb-${d}-${sha256Hex(spec.id).slice(0, 4)}`;
}

/** Rows a launch generates for a table: users = num_rows, children by the mean fan-out. */
export function rowsFor(spec: Pick<EvalSpec, "numRows">, table: TableDef): number {
  if (table.name === "users" || table.role === "isolated") return spec.numRows;
  if (table.name === "orders") return Math.round(spec.numRows * 1.25);
  if (table.name === "order_items") return Math.round(spec.numRows * 1.25 * 1.448);
  return table.sourceRows;
}

export const isoSeconds = (ms: number) => new Date(ms).toISOString().replace(".000Z", "Z");

/** A Dataflow-style job id: YYYY-MM-DD_HH_MM_SS-<19 digits>. */
export function dataflowJobId(atMs: number, draw: (lo: number, hi: number) => number): string {
  const d = new Date(atMs).toISOString();
  const stamp = `${d.slice(0, 10)}_${d.slice(11, 13)}_${d.slice(14, 16)}_${d.slice(17, 19)}`;
  const digits = Array.from({ length: 19 }, (_, i) => draw(i === 0 ? 1 : 0, 9)).join("");
  return `${stamp}-${digits}`;
}
