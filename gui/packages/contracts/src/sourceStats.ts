/**
 * `source_table_stats.stats`: the profiler's full per-column entry, stored as a
 * JSON string. Key names mirror `sdfb_core/stats/source_stats.py`
 * (`profile_source_table`, PROFILER_VERSION "2") and the exact-tier merge in
 * `sdfb_beam/io/exact_stats.py`, which adds `source_rows` and overwrites the
 * fractions, `distinct`, `deciles`, `mean`/`stddev` and `top_values` with
 * full-table values. The `__table__` pseudo-column carries only the null-pattern
 * mix. Unknown keys are kept (`loose`) so a newer profiler never breaks the GUI.
 *
 * Snapshots: the profiler skips a write only when (table, reference_digest,
 * profiler_version, stats_tier) already exists (`stats_store.exists`), so one
 * digest can carry a sample AND an exact snapshot, and several profiler
 * versions. A snapshot is therefore keyed by (digest, tier, profiler_version,
 * run_id) — `snapshotKey` — never by the digest alone. A NULL `stats_tier`
 * (rows written before the column existed) is the sample tier, everywhere.
 */
import { z } from "zod";

const fraction = z.number().min(0).max(1);

export const profilerStatsSchema = z
  .object({
    type: z.string(),
    in_source_schema: z.boolean(),
    sample_rows: z.int().nonnegative(),
    null_fraction: fraction,
    empty_fraction: fraction,
    zero_fraction: fraction.optional(),
    distinct: z.int().nonnegative(),
    distinct_ratio: z.number().nonnegative(),
    is_constant: z.boolean().optional(),
    is_pk: z.boolean(),
    is_fk: z.boolean(),
    identity_col: z.boolean(),
    min: z.union([z.number(), z.string()]).nullable().optional(),
    max: z.union([z.number(), z.string()]).nullable().optional(),
    mean: z.number().nullable().optional(),
    stddev: z.number().nullable().optional(),
    /** 11 points, p0..p100 (empty for non-numeric columns). */
    deciles: z.array(z.number()).optional(),
    /** Shannon entropy in bits over the observed values. */
    entropy: z.number().nullable().optional(),
    /** entropy / log2(distinct): 1 = uniform over the observed support. */
    entropy_norm: z.number().nullable().optional(),
    top1_share: fraction.nullable().optional(),
    /** [value, share] pairs, only when distinct ≤ 50 (literal values of enum-like columns). */
    top_values: z.array(z.tuple([z.string(), fraction])).optional(),
    len_p05: z.int().nullable().optional(),
    len_p50: z.int().nullable().optional(),
    len_p95: z.int().nullable().optional(),
    mean_len: z.number().nullable().optional(),
    /** [mask, share] pairs (9 digit, A upper, a lower). */
    shape_mix: z.array(z.tuple([z.string(), fraction])).optional(),
    temporal_day_granularity: z.boolean().optional(),
    temporal_min: z.string().nullable().optional(),
    temporal_max: z.string().nullable().optional(),
    /** Monday first; shares. */
    dow_mix: z.array(fraction).optional(),
    hour_mix: z.array(fraction).optional(),
    month_mix: z.array(fraction).optional(),
    future_fraction: fraction.nullable().optional(),
    generation_plan: z.string(),
    /** Absent in entries written before the exact tier existed: those are sample-tier (see `effectiveTier`). */
    stats_tier: z.enum(["sample", "exact"]).optional(),
    profiler_version: z.string().optional(),
    /** Exact tier only: the full-table row count. */
    source_rows: z.int().nonnegative().optional(),
    /** `__table__` only. */
    null_pattern_columns: z.array(z.string()).optional(),
    null_pattern_mix: z.array(z.tuple([z.string(), fraction])).optional(),
  })
  .loose();

export type ProfilerStats = z.infer<typeof profilerStatsSchema>;

/** The `__table__` pseudo-column name (`TABLE_PSEUDO_COLUMN`). */
export const TABLE_PSEUDO_COLUMN = "__table__";

export type StatsTier = "sample" | "exact";

/** A NULL / absent `stats_tier` is a legacy sample-tier row. */
export function effectiveTier(tier: string | null | undefined): StatsTier {
  return tier === "exact" ? "exact" : "sample";
}

/** One profiler snapshot: `<reference_digest>|<tier>|<profiler_version or "">|<run_id>`. */
export function snapshotKey(row: {
  reference_digest: string;
  stats_tier: string | null | undefined;
  profiler_version: string | null | undefined;
  run_id: string;
}): string {
  return [row.reference_digest, effectiveTier(row.stats_tier), row.profiler_version ?? "", row.run_id].join("|");
}
