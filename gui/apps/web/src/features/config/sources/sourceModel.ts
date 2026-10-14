/**
 * Pure helpers for the source-stats explorer: snapshot grouping, the tier
 * compare, the quantile function with its DKW band, null-pattern decoding and
 * the literal policy. Degenerate rows (no stats, empty deciles, a legacy NULL
 * tier) return null or empty, never NaN.
 */
import type { SourceStatsColumn, SourceStatsSnapshot } from "@contracts/api";
import type { ProfilerStats } from "@contracts/sourceStats";
import { dkwEpsilon, wilson } from "@synthetic-platform/stats";

export type Tier = "sample" | "exact";

/** The profiler's `__table__` pseudo-column (`TABLE_PSEUDO_COLUMN`; typed here so the tab ships no zod). */
export const TABLE_PSEUDO_COLUMN = "__table__";

/** Digests profiled on both tiers (the tier compare of one reference sample), newest first. */
export function digestsWithBothTiers(snapshots: readonly SourceStatsSnapshot[]): string[] {
  const tiers = new Map<string, Set<Tier>>();
  for (const s of snapshots) {
    const set = tiers.get(s.reference_digest) ?? new Set<Tier>();
    set.add(s.tier);
    tiers.set(s.reference_digest, set);
  }
  const out: string[] = [];
  for (const s of snapshots) {
    if (tiers.get(s.reference_digest)?.size === 2 && !out.includes(s.reference_digest)) out.push(s.reference_digest);
  }
  return out;
}

export function shortDigest(digest: string): string {
  return digest.slice(0, 12);
}

/**
 * The values a column's deciles are computed over: the substantive rows,
 * n·(1 − null − empty) (source_stats.py `_add_numeric` sorts only non-null,
 * non-empty values). The DKW band of the deciles uses this n; the null-rate
 * Wilson interval keeps all rows.
 */
export function decileValueCount(row: {
  sample_rows: number | null;
  null_fraction: number | null;
  empty_fraction: number | null;
}): number {
  const rows = row.sample_rows ?? 0;
  const share = 1 - (row.null_fraction ?? 0) - (row.empty_fraction ?? 0);
  return Math.max(0, Math.round(rows * Math.min(1, Math.max(0, share))));
}

/** Linear interpolation through an ascending 11-point decile vector at rank p ∈ [0, 1]. */
export function quantileAt(deciles: readonly number[], p: number): number | null {
  if (deciles.length < 2 || deciles.some((d) => !Number.isFinite(d))) return null;
  const x = Math.min(1, Math.max(0, p)) * (deciles.length - 1);
  const i = Math.min(deciles.length - 2, Math.floor(x));
  const t = x - i;
  return deciles[i]! + t * (deciles[i + 1]! - deciles[i]!);
}

export type QuantileRow = {
  p: number;
  sample: number | null;
  exact: number | null;
  /** The DKW band on the sample tier, in rank space: Q(p − ε) … Q(p + ε). */
  bandBase: number | null;
  bandSpan: number | null;
};

/**
 * The quantile function of each tier on a shared rank grid, with the sample
 * tier's DKW band: the true p-quantile lies between Q̂(p − ε) and Q̂(p + ε)
 * (interpolated between the 11 stored points).
 */
export function quantileRows(
  sample: { deciles: readonly number[]; rows: number } | null,
  exact: { deciles: readonly number[] } | null,
  alpha = 0.05,
): QuantileRow[] {
  const eps = sample && sample.rows > 0 ? dkwEpsilon(sample.rows, alpha) : 0;
  return Array.from({ length: 51 }, (_, i) => {
    const p = i / 50;
    const lo = sample ? quantileAt(sample.deciles, p - eps) : null;
    const hi = sample ? quantileAt(sample.deciles, p + eps) : null;
    return {
      p,
      sample: sample ? quantileAt(sample.deciles, p) : null,
      exact: exact ? quantileAt(exact.deciles, p) : null,
      bandBase: lo,
      bandSpan: lo !== null && hi !== null ? hi - lo : null,
    };
  });
}

/** A share with its 95 % Wilson interval (sample tier only; the exact tier is the full table). */
export function shareInterval(share: number | null | undefined, rows: number | null | undefined, tier: Tier) {
  if (share === null || share === undefined || !Number.isFinite(share)) return null;
  if (tier === "exact" || !rows || rows <= 0) return { share, low: null, high: null };
  const [low, high] = wilson(Math.round(share * rows), rows);
  return { share, low, high };
}

/** Literal labels are shown only for enum-like columns; `h:<8 hex>` labels are hashed (core:literal-policy). */
export function isHashedLabel(label: string): boolean {
  return /^h:[0-9a-f]{8}$/i.test(label);
}

/** "00000010000" + the pattern's columns → the columns that are null together. */
export function decodeNullPattern(bits: string, columns: readonly string[]): string[] {
  return [...bits].flatMap((bit, i) => (bit === "1" && columns[i] ? [columns[i]] : []));
}

export function isTableRow(column: string): boolean {
  return column === TABLE_PSEUDO_COLUMN;
}

export type CompareRow = {
  column: string;
  plan: string | null;
  sampleDistinct: number | null;
  exactDistinct: number | null;
  /** exact / sample: how far the sample truncated the count. */
  truncation: number | null;
  sampleNull: number | null;
  exactNull: number | null;
  /** |Δ null| within the sample tier's DKW band. */
  nullWithinBand: boolean | null;
  sampleEntropy: number | null;
  exactEntropy: number | null;
};

/** One row per column present in either tier's snapshot (the __table__ row excluded). */
export function compareTiers(
  columns: readonly SourceStatsColumn[],
  sampleKey: string,
  exactKey: string,
  sampleRows: number,
): CompareRow[] {
  const byKey = (key: string) =>
    new Map(columns.filter((c) => c.snapshot_key === key && !isTableRow(c.column)).map((c) => [c.column, c]));
  const s = byKey(sampleKey);
  const e = byKey(exactKey);
  const names = [...new Set([...s.keys(), ...e.keys()])].sort();
  const eps = sampleRows > 0 ? dkwEpsilon(sampleRows) : null;
  return names.map((column) => {
    const a = s.get(column);
    const b = e.get(column);
    const sampleNull = a?.null_fraction ?? null;
    const exactNull = b?.null_fraction ?? null;
    const sd = a?.distinct ?? null;
    const ed = b?.distinct ?? null;
    return {
      column,
      plan: a?.generation_plan ?? b?.generation_plan ?? null,
      sampleDistinct: sd,
      exactDistinct: ed,
      truncation: sd && ed ? ed / sd : null,
      sampleNull,
      exactNull,
      nullWithinBand:
        sampleNull !== null && exactNull !== null && eps !== null ? Math.abs(sampleNull - exactNull) <= eps : null,
      sampleEntropy: stat(a, "entropy"),
      exactEntropy: stat(b, "entropy"),
    };
  });
}

function stat(column: SourceStatsColumn | undefined, key: keyof ProfilerStats): number | null {
  const v = column?.stats_parsed?.[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
