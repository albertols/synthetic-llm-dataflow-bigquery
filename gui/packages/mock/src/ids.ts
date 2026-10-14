/** Identifiers shared by the mock's tables: reference digests, run ids, per-table row counts. */
import { sha256Hex } from "@synthetic-platform/stats";

import type { EvalSpec } from "./storyline";
import { sourceFqn, type TableDef } from "./thelook";

/**
 * The source snapshot a launch samples from: the mock's source tables are re-snapshotted
 * on 2026-09-01, so every launch before that samples the August snapshot.
 */
export function snapshotEra(evaluatedAt: string): "2026-08" | "2026-09" {
  return evaluatedAt < "2026-09-01" ? "2026-08" : "2026-09";
}

/**
 * The reference digest a launch computes: one per (table, sample size, source snapshot).
 * The stats tier is NOT part of it — as in the pipeline, one digest can be profiled on
 * the sample tier by one launch and on the exact tier by a later one.
 */
export function referenceDigest(table: string, referenceRowsLimit: number, era: "2026-08" | "2026-09"): string {
  return sha256Hex(`${sourceFqn(table)}|${referenceRowsLimit}|${era}-snapshot`);
}

/** The digest a launch's table samples from. */
export const launchDigest = (spec: Pick<EvalSpec, "evaluatedAt" | "referenceRowsLimit">, table: string) =>
  referenceDigest(table, spec.referenceRowsLimit, snapshotEra(spec.evaluatedAt));

/** The launch's root run id (run_pipeline --run_id). */
export function baseRunId(spec: Pick<EvalSpec, "id" | "evaluatedAt">): string {
  const d = spec.evaluatedAt.slice(0, 16).replace(/[-:]/g, "").replace("T", "-");
  return `sdfb-${d}-${sha256Hex(spec.id).slice(0, 4)}`;
}

/**
 * Per-table run ids as `run_pipeline.plan_launch` writes them: a one-table launch keeps
 * the base id; a multi-table launch appends `-NN-<table>` in generation order (NN from 00).
 */
export function tableRunIds(base: string, tablesInGenerationOrder: readonly string[]): Map<string, string> {
  const multi = tablesInGenerationOrder.length > 1;
  return new Map(
    tablesInGenerationOrder.map((t, i) => [t, multi ? `${base}-${String(i).padStart(2, "0")}-${t}` : base]),
  );
}

/** Rows a launch generates for a table: users = num_rows, children by the mean fan-out. */
export function rowsFor(spec: Pick<EvalSpec, "numRows">, table: TableDef): number {
  if (table.name === "users" || table.role === "isolated") return spec.numRows;
  if (table.name === "orders") return Math.round(spec.numRows * 1.25);
  if (table.name === "order_items") return Math.round(spec.numRows * 1.25 * 1.448);
  return table.sourceRows;
}

/** Epoch ms → the canonical wire timestamp (six fraction digits), keeping `micros` extra microseconds. */
export const isoMicros = (ms: number, micros = 0) =>
  `${new Date(Math.floor(ms)).toISOString().slice(0, 19)}.${String((Math.floor(ms) % 1000) * 1000 + micros).padStart(6, "0")}Z`;

/** A Dataflow-style job id: YYYY-MM-DD_HH_MM_SS-<19 digits>. */
export function dataflowJobId(atMs: number, draw: (lo: number, hi: number) => number): string {
  const d = new Date(atMs).toISOString();
  const stamp = `${d.slice(0, 10)}_${d.slice(11, 13)}_${d.slice(14, 16)}_${d.slice(17, 19)}`;
  const digits = Array.from({ length: 19 }, (_, i) => draw(i === 0 ? 1 : 0, 9)).join("");
  return `${stamp}-${digits}`;
}
