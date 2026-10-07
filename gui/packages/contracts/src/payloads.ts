/**
 * `evaluation_profiles.payload` and `evaluation_metrics.detail`, per kind.
 *
 * The BigQuery column is `JSON` (the generated row schema says `z.json()`), so
 * the evaluator owns the exact shape: its producers (`beam/dense.py`,
 * `beam/census.py`, `beam/privacy.py`) write the shapes below (Ruling R27).
 * Profiles honour the D6 literal policy: a value is a literal only when its
 * column has ≤ 50 source-distinct values and the value a source count ≥ 10;
 * otherwise it is a keyed-hash label `h:<8 hex>`. A payload may carry keys
 * these schemas do not list (a histogram's `below_mass` / `above_mass` /
 * `extremes`, for one); they are ignored. The mock produces exactly these
 * shapes; a live payload that does not parse must be shown as "profile
 * unavailable", never crash a chart: use `parseProfile`.
 *
 * Bins follow the evaluator's `bin_counts`: `edges` are interior edges and
 * `counts` has `edges.length + 1` entries, (-inf, e0], (e0, e1], …, (e_last, +inf).
 */
import { z } from "zod";

const count = z.int().nonnegative();
const share = z.number().min(0).max(1);

/** Numeric/temporal histogram on the source's equiprobable edges (temporal: epoch seconds). */
export const histogramPayloadSchema = z.object({
  edges: z.array(z.number()),
  counts: z.array(count),
  /** The side's extremes, to draw the open-ended tail bins; null when the count rule withholds them. */
  min: z.number().nullable(),
  max: z.number().nullable(),
  nulls: count,
  /** "value" (column units) or "epoch_seconds" (temporal). */
  unit: z.enum(["value", "epoch_seconds"]),
});

export const quantilesPayloadSchema = z.object({
  probs: z.array(share),
  values: z.array(z.number()),
  unit: z.enum(["value", "epoch_seconds"]),
});

export const topkPayloadSchema = z.object({
  items: z.array(
    z.object({
      /** The literal value, or `h:<8 hex>` when the D6 policy hashes it. */
      label: z.string(),
      literal: z.boolean(),
      count,
      share,
    }),
  ),
  /** Rows outside `items`. */
  other_count: count,
  distinct: count,
  total: count,
  nulls: count,
});

export const lengthHistPayloadSchema = z.object({
  /** String lengths 0..255; `overflow` counts lengths ≥ 256. */
  lengths: z.array(z.int().nonnegative()),
  counts: z.array(count),
  overflow: count,
});

export const shapeMixPayloadSchema = z.object({
  /** Head masks (9 = digit, A = upper, a = lower, other characters literal) holding ≥ head_floor of the mass. */
  items: z.array(z.object({ mask: z.string(), count, share })),
  tail_share: share,
  head_floor: share,
});

export const charClassesPayloadSchema = z.object({
  /** Share of non-null values containing at least one character of each class. */
  classes: z.object({ upper: share, lower: share, digit: share, space: share, punct: share, other: share }),
  n: count,
});

export const temporalMixPayloadSchema = z.object({
  /** Monday first (Python `weekday()`). */
  dow: z.array(count).length(7),
  month: z.array(count).length(12),
  /** Empty at day granularity. */
  hour: z.array(count),
  day_granularity: z.boolean(),
});

export const nullPatternsPayloadSchema = z.object({
  columns: z.array(z.string()),
  /** `bits[i] === "1"` when `columns[i]` is null in that pattern. */
  patterns: z.array(z.object({ bits: z.string(), count, share })),
  overflow: count,
});

export const corrMatrixPayloadSchema = z.object({
  columns: z.array(z.string()),
  method: z.enum(["pearson", "spearman", "cramers_v"]),
  /** Row-major, symmetric, diagonal 1; null where undefined (zero variance, too few pairs). */
  values: z.array(z.array(z.number().nullable())),
  n: count,
});

export const contingencyPayloadSchema = z.object({
  column_x: z.string(),
  column_y: z.string(),
  x_labels: z.array(z.string()),
  y_labels: z.array(z.string()),
  /** counts[i][j] for x_labels[i] × y_labels[j]. */
  counts: z.array(z.array(count)),
});

/** Distance to closest record (Gower, 0..1) or NNDR histograms: side "synthetic" = syn→R, "holdout" = H→R. */
export const distanceHistPayloadSchema = z.object({
  edges: z.array(z.number()),
  counts: z.array(count),
  p5: z.number().nullable(),
  p50: z.number().nullable(),
  n: count,
});

export const rocCurvePayloadSchema = z.object({
  /** [false positive rate, true positive rate], ascending FPR, (0,0) … (1,1). */
  points: z.array(z.tuple([share, share])),
  auc: share,
  ci_low: share.nullable(),
  ci_high: share.nullable(),
  n_source: count,
  n_synthetic: count,
});

export const momentsPayloadSchema = z.object({
  n: count,
  mean: z.number().nullable(),
  std: z.number().nullable(),
  skewness: z.number().nullable(),
  kurtosis_excess: z.number().nullable(),
  min: z.number().nullable(),
  max: z.number().nullable(),
  zeros: count,
  unit: z.enum(["value", "epoch_seconds"]),
});

export const profilePayloadSchemas = {
  histogram: histogramPayloadSchema,
  quantiles: quantilesPayloadSchema,
  topk: topkPayloadSchema,
  length_hist: lengthHistPayloadSchema,
  shape_mix: shapeMixPayloadSchema,
  char_classes: charClassesPayloadSchema,
  temporal_mix: temporalMixPayloadSchema,
  null_patterns: nullPatternsPayloadSchema,
  corr_matrix: corrMatrixPayloadSchema,
  contingency: contingencyPayloadSchema,
  dcr_hist: distanceHistPayloadSchema,
  nndr_hist: distanceHistPayloadSchema,
  roc_curve: rocCurvePayloadSchema,
  moments: momentsPayloadSchema,
} as const;

export type ProfileKind = keyof typeof profilePayloadSchemas;
export type ProfilePayload<K extends ProfileKind> = z.infer<(typeof profilePayloadSchemas)[K]>;
export type HistogramPayload = ProfilePayload<"histogram">;
export type QuantilesPayload = ProfilePayload<"quantiles">;
export type TopkPayload = ProfilePayload<"topk">;
export type DistanceHistPayload = ProfilePayload<"dcr_hist">;
export type RocCurvePayload = ProfilePayload<"roc_curve">;
export type CorrMatrixPayload = ProfilePayload<"corr_matrix">;
export type ContingencyPayload = ProfilePayload<"contingency">;

/** The typed payload, or null when the kind is unknown or the payload does not match (show "profile unavailable"). */
export function parseProfile<K extends ProfileKind>(kind: K, payload: unknown): ProfilePayload<K> | null {
  const schema = profilePayloadSchemas[kind] as unknown as z.ZodType<ProfilePayload<K>> | undefined;
  if (!schema) return null;
  const parsed = schema.safeParse(payload);
  return parsed.success ? parsed.data : null;
}

/**
 * `evaluation_metrics.detail`: every key optional, unknown keys kept. The
 * common ones: `reason` (why not_evaluated), `d_hi` (the KS bracket's upper
 * end), `tail_mass_*`, `copies`/`expected` (lifts: the rate-ratio counts),
 * `matched_n`, `chao_shen`.
 */
export const metricDetailSchema = z
  .object({
    reason: z.string().optional(),
    d_lo: z.number().optional(),
    d_hi: z.number().optional(),
    matched_n: z.int().optional(),
    copies_r: z.int().optional(),
    copies_h: z.int().optional(),
    lift: z.number().nullable().optional(),
    note: z.string().optional(),
  })
  .loose();
export type MetricDetail = z.infer<typeof metricDetailSchema>;
