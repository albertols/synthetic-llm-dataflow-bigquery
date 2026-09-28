/**
 * `source_table_stats` rows: the profiler's per-column entries
 * (`sdfb_core/stats/source_stats.py`, PROFILER_VERSION "2") for every source
 * table and reference digest, on the sample tier (fractions and distinct from
 * the reference sample) and the exact tier (the full table; distinct from the
 * whole source). Computed from the mock's source rows with packages/stats.
 *
 * Snapshots are what the pipeline writes: the profiler skips (table, digest,
 * profiler_version, tier) only, so the August digests carry a sample snapshot
 * (the first August launch) AND an exact one (the 2026-08-31 launch, the first
 * on the exact tier); and `users` keeps one legacy snapshot from profiler
 * version "1", written before `stats_tier` existed (NULL tier = sample).
 */
import type { SourceTableStatsRow } from "@synthetic-platform/contracts";
import {
  entropyBits,
  expectedDistinct,
  normalize,
  profilerDeciles,
  seedFrom,
  sha256Hex,
  Random,
} from "@synthetic-platform/stats";

import { baseRunId, isoMicros, launchDigest, tableRunIds } from "./ids";
import { STORYLINE } from "./storyline";
import { epoch, textUniverse } from "./synth";
import { sourceFqn, TABLES, type ColumnDef, type Row, type TableDef } from "./thelook";

const PROFILER_VERSION = "2";
const TOP_VALUES_MAX_DISTINCT = 50;

/**
 * The reference sample as the pipeline reads it: distinct rows (a SELECT … LIMIT never
 * repeats one, so a PK stays unique), here a seeded 80 % subset drawn without
 * replacement — the part of the table the sample tier sees, noisy against the exact tier.
 */
function referenceSample(rows: readonly Row[], rng: Random): Row[] {
  const pool = [...rows];
  const size = Math.round(rows.length * 0.8);
  for (let i = 0; i < size; i += 1) {
    const j = rng.int(i, pool.length - 1);
    [pool[i], pool[j]] = [pool[j]!, pool[i]!];
  }
  return pool.slice(0, size);
}

const mask = (v: string) => v.replace(/[0-9]/g, "9").replace(/[A-Z]/g, "A").replace(/[a-z]/g, "a");
const round = (v: number, digits = 6) => Math.round(v * 10 ** digits) / 10 ** digits;

function entry(table: TableDef, column: ColumnDef, rows: readonly Row[], tier: "sample" | "exact", sampleRows: number) {
  const values = rows.map((r) => r[column.name] ?? null);
  const n = values.length;
  const nonNull = values.filter((v) => v !== null);
  const empties = nonNull.filter((v) => typeof v === "string" && v.trim() === "").length;
  const substantive = nonNull.filter((v) => !(typeof v === "string" && v.trim() === "")).map(String);
  const counter = new Map<string, number>();
  for (const v of substantive) counter.set(v, (counter.get(v) ?? 0) + 1);
  const universe = textUniverse(table.name, column.name);
  const population = tier === "exact" ? table.sourceRows : sampleRows;
  let distinct: number;
  if (universe)
    distinct = Math.round(expectedDistinct(normalize(universe.weights), population * (substantive.length / n)));
  else if (counter.size > substantive.length * 0.5)
    distinct = Math.round((counter.size / substantive.length) * population * (substantive.length / n));
  else distinct = counter.size;
  const counts = [...counter.values()];
  const entropy = entropyBits(counts);
  const sorted = [...counter.entries()].sort((a, b) => b[1] - a[1]);
  const out: Record<string, unknown> = {
    type: column.bqType,
    in_source_schema: true,
    sample_rows: tier === "exact" ? table.sourceRows : sampleRows,
    null_fraction: round((n - nonNull.length) / n),
    empty_fraction: round(empties / n),
    zero_fraction: 0,
    distinct,
    distinct_ratio: round(distinct / (tier === "exact" ? table.sourceRows : sampleRows)),
    is_constant: counter.size <= 1 && !empties && nonNull.length === n,
    is_pk: column.role === "pk",
    is_fk: column.role === "fk",
    identity_col: column.role === "pk",
    min: null,
    max: null,
    mean: null,
    stddev: null,
    deciles: [],
    entropy: entropy === null ? null : round(entropy, 4),
    entropy_norm: entropy !== null && counter.size > 1 ? round(entropy / Math.log2(counter.size), 4) : 0,
    top1_share: sorted.length ? round(sorted[0]![1] / substantive.length) : null,
    top_values:
      counter.size <= TOP_VALUES_MAX_DISTINCT
        ? sorted.slice(0, 8).map(([v, c]) => [v, round(c / substantive.length)])
        : [],
    len_p05: null,
    len_p50: null,
    len_p95: null,
    mean_len: null,
    shape_mix: [],
    temporal_day_granularity: false,
    temporal_min: null,
    temporal_max: null,
    dow_mix: [],
    hour_mix: [],
    month_mix: [],
    future_fraction: null,
    generation_plan: column.plan === "key" ? "" : column.plan,
    stats_tier: tier,
    profiler_version: PROFILER_VERSION,
  };
  if (tier === "exact") out.source_rows = table.sourceRows;
  if (column.kind === "numeric" || (column.role && typeof nonNull[0] === "number")) {
    const numbers = nonNull
      .map(Number)
      .filter(Number.isFinite)
      .sort((a, b) => a - b);
    if (numbers.length) {
      const mean = numbers.reduce((a, b) => a + b, 0) / numbers.length;
      out.min = numbers[0];
      out.max = numbers[numbers.length - 1];
      out.zero_fraction = round(numbers.filter((v) => v === 0).length / numbers.length);
      out.mean = round(mean);
      out.stddev = round(Math.sqrt(numbers.reduce((a, b) => a + (b - mean) ** 2, 0) / numbers.length));
      out.deciles = profilerDeciles(numbers).map((v) => round(v));
    }
  } else if (substantive.length) {
    const lengths = substantive.map((s) => s.length).sort((a, b) => a - b);
    const last = lengths.length - 1;
    out.len_p05 = lengths[Math.floor(0.05 * last)];
    out.len_p50 = lengths[Math.floor(0.5 * last)];
    out.len_p95 = lengths[Math.floor(0.95 * last)];
    out.mean_len = round(lengths.reduce((a, b) => a + b, 0) / lengths.length, 2);
    if (column.kind === "temporal") {
      const times = substantive.map((s) => epoch(s)).sort((a, b) => a - b);
      const dow = new Array<number>(7).fill(0);
      const month = new Array<number>(12).fill(0);
      const hour = new Array<number>(24).fill(0);
      for (const t of times) {
        const d = new Date(t * 1000);
        dow[(d.getUTCDay() + 6) % 7]! += 1;
        month[d.getUTCMonth()]! += 1;
        hour[d.getUTCHours()]! += 1;
      }
      out.temporal_min = new Date(times[0]! * 1000).toISOString().replace(".000Z", "+00:00");
      out.temporal_max = new Date(times[times.length - 1]! * 1000).toISOString().replace(".000Z", "+00:00");
      out.dow_mix = dow.map((c) => round(c / times.length, 4));
      out.month_mix = month.map((c) => round(c / times.length, 4));
      out.hour_mix = hour.map((c) => round(c / times.length, 4));
      out.future_fraction = 0;
    } else {
      const masks = new Map<string, number>();
      for (const s of substantive) masks.set(mask(s), (masks.get(mask(s)) ?? 0) + 1);
      out.shape_mix = [...masks.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 8)
        .map(([m, c]) => [m, round(c / substantive.length, 4)]);
    }
  }
  return out;
}

function tableEntry(table: TableDef, rows: readonly Row[], tier: "sample" | "exact", sampleRows: number) {
  const names = table.columns.map((c) => c.name);
  const patterns = new Map<string, number>();
  for (const r of rows) {
    const bits = names.map((c) => (r[c] === null || r[c] === undefined ? "1" : "0")).join("");
    patterns.set(bits, (patterns.get(bits) ?? 0) + 1);
  }
  return {
    type: "",
    in_source_schema: false,
    sample_rows: tier === "exact" ? table.sourceRows : sampleRows,
    null_fraction: 0,
    empty_fraction: 0,
    distinct: patterns.size,
    distinct_ratio: round(patterns.size / rows.length),
    is_pk: false,
    is_fk: false,
    identity_col: false,
    generation_plan: "",
    null_pattern_columns: names,
    null_pattern_mix: [...patterns.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([bits, c]) => [bits, round(c / rows.length)]),
    stats_tier: tier,
    profiler_version: PROFILER_VERSION,
  };
}

/** The legacy snapshot: profiler "1", before the exact tier (no stats_tier / profiler_version keys). */
const LEGACY = { table: "users", limit: 10_000, era: "2026-07", at: isoMicros(Date.UTC(2026, 6, 28, 9, 12), 734) };

export function buildSourceStats(samples: Record<string, { source: Row[] }>): SourceTableStatsRow[] {
  const out: SourceTableStatsRow[] = [];
  const seen = new Set<string>();
  const firstRun = new Map<string, { run: string; at: string }>();
  for (const spec of STORYLINE) {
    const generated = spec.relational ? ["users", "orders", "order_items"] : spec.tables;
    const runIds = tableRunIds(baseRunId(spec), generated);
    // The external catalog is profiled by the launch of the child that draws from it.
    const tables = spec.relational ? [...generated, "products"] : generated;
    for (const name of tables) {
      const digest = launchDigest(spec, name);
      const tier = spec.sourceStatsTier;
      const key = `${name}|${digest}|${tier}`;
      if (!firstRun.has(key))
        firstRun.set(key, {
          run: runIds.get(name) ?? runIds.get("order_items")!,
          at: isoMicros(Date.parse(spec.evaluatedAt) - 140 * 60_000, 318),
        });
      if (seen.has(key)) continue;
      seen.add(key);
      const table = TABLES[name]!;
      const rows = samples[name]!.source;
      // The sample tier profiles the reference sample; a seeded subset of the mock rows stands in for it.
      const rng = new Random(seedFrom("stats", key));
      const profiled = tier === "exact" ? rows : referenceSample(rows, rng);
      const { run, at } = firstRun.get(key)!;
      const push = (column: string, stats: Record<string, unknown>) =>
        out.push({
          table_fqn: sourceFqn(name),
          reference_digest: digest,
          run_id: run,
          column,
          generation_plan: (stats.generation_plan as string) || null,
          null_fraction: stats.null_fraction as number,
          empty_fraction: stats.empty_fraction as number,
          distinct: stats.distinct as number,
          distinct_ratio: stats.distinct_ratio as number,
          is_pk: stats.is_pk as boolean,
          is_fk: stats.is_fk as boolean,
          stats: JSON.stringify(stats, Object.keys(stats).sort()),
          sample_rows: stats.sample_rows as number,
          stats_tier: tier,
          profiler_version: PROFILER_VERSION,
          computed_at: at,
        });
      for (const column of table.columns)
        push(column.name, entry(table, column, profiled, tier, spec.referenceRowsLimit));
      if (table.columns.length <= 64) push("__table__", tableEntry(table, profiled, tier, spec.referenceRowsLimit));
    }
  }
  // Profiler "1" wrote no tier: the row's stats_tier is NULL and the entry lacks both keys.
  const table = TABLES[LEGACY.table]!;
  const digest = sha256Hex(`${sourceFqn(LEGACY.table)}|${LEGACY.limit}|${LEGACY.era}-snapshot`);
  const rng = new Random(seedFrom("stats", "legacy", LEGACY.table));
  const rows = samples[LEGACY.table]!.source;
  const profiled = referenceSample(rows, rng);
  for (const column of table.columns) {
    const { stats_tier: _t, profiler_version: _v, ...stats } = entry(table, column, profiled, "sample", LEGACY.limit);
    out.push({
      table_fqn: sourceFqn(LEGACY.table),
      reference_digest: digest,
      run_id: "sdfb-20260728-0905-legacy",
      column: column.name,
      generation_plan: (stats.generation_plan as string) || null,
      null_fraction: stats.null_fraction as number,
      empty_fraction: stats.empty_fraction as number,
      distinct: stats.distinct as number,
      distinct_ratio: stats.distinct_ratio as number,
      is_pk: stats.is_pk as boolean,
      is_fk: stats.is_fk as boolean,
      stats: JSON.stringify(stats, Object.keys(stats).sort()),
      sample_rows: LEGACY.limit,
      stats_tier: null,
      profiler_version: null,
      computed_at: LEGACY.at,
    });
  }
  return out;
}
