/**
 * Catalogue lookups for the EVALUATION views. The catalogue is the generated
 * copy of metrics.yaml (`@contracts/generated/catalogue`); metric rows carry
 * their own thresholds and noise floors, which always win over it (a row may
 * come from another catalogue version).
 */
import { catalogue, catalogueById, catalogueVersion, type CatalogueMetric } from "@contracts/generated/catalogue";
import { isAggregateMetric, zeroToleranceIds } from "@synthetic-platform/stats/scoring";

import type { ConceptLevel } from "@/lib/concepts";

export { catalogueVersion };

export const FAMILIES = ["fidelity", "privacy", "integrity", "diversity"] as const;
export type Family = (typeof FAMILIES)[number];

export const FAMILY_LABEL: Record<string, string> = {
  fidelity: "Fidelity",
  privacy: "Privacy",
  integrity: "Integrity",
  diversity: "Diversity",
  overall: "Overall",
};

export const FAMILY_QUESTION: Record<string, string> = {
  overall: "Mean of the available family scores.",
  fidelity: "Does the synthetic data look like the source?",
  privacy: "Does it copy the rows the generator read?",
  integrity: "Are keys, types and references valid?",
  diversity: "Does it keep the source's variety?",
};

export const LEVEL_ORDER_LIST: readonly ConceptLevel[] = [
  "field",
  "column",
  "pair",
  "row",
  "table",
  "relationship",
  "model",
];

export function isLevel(value: string): value is ConceptLevel {
  return (LEVEL_ORDER_LIST as readonly string[]).includes(value);
}

/** The catalogue entry for a metric id, or undefined for an id this build does not know. */
export function metricMeta(id: string): CatalogueMetric | undefined {
  return (catalogueById as Readonly<Record<string, CatalogueMetric>>)[id];
}

/** `metric:<id>` when the catalogue knows the id (the InfoHint concept), else undefined. */
export function metricConcept(id: string): string | undefined {
  return metricMeta(id) ? `metric:${id}` : undefined;
}

/** Position in the catalogue (stable column order in the heatmap). */
const ORDER = new Map(Object.keys(catalogueById).map((id, index) => [id, index]));
export function catalogueIndex(id: string): number {
  return ORDER.get(id) ?? Number.MAX_SAFE_INTEGER;
}

/** Short column headers for the heatmap; the (i) next to each carries the full title. */
const SHORT: Record<string, string> = {
  "field.category_adherence": "Category adh.",
  "field.range_adherence": "Range adh.",
  "field.shape_adherence": "Shape adh.",
  "field.substantive_copy_rate": "Copy rate",
  "field.value_memorization_lift": "Value lift",
  "field.pool_memorization_lift": "Pool lift",
  "field.type_validity": "Type valid",
  "column.null_rate_delta": "Null Δ",
  "column.empty_rate_delta": "Empty Δ",
  "column.ks": "KS",
  "column.pit_w1": "PIT-W1",
  "column.wasserstein": "W1",
  "column.decile_ks_legacy": "Decile KS",
  "column.jsd": "JSD",
  "column.tvd": "TVD",
  "column.psi": "PSI",
  "column.smd": "SMD",
  "column.std_ratio": "SD ratio",
  "column.zero_rate_delta": "Zero Δ",
  "column.range_coverage": "Range cov.",
  "column.dow_tvd": "Weekday",
  "column.month_tvd": "Month",
  "column.hour_tvd": "Hour",
  "column.cohens_w": "Cohen's w",
  "column.top1_share_delta": "Top-1 Δ",
  "column.coverage_mass": "Coverage",
  "column.novelty_mass": "Novelty",
  "column.entropy_ratio": "Entropy",
  "column.distinct_ratio": "Distinct",
  "column.distinct_ceiling_hit": "Pool cap",
  "column.length_ks": "Length KS",
  "column.shape_head_tv": "Shape TV",
  "column.char_class_l1": "Char L1",
  "column.source_stats_drift": "Stats drift",
  "pair.pearson_delta": "Pearson Δ",
  "pair.spearman_delta": "Spearman Δ",
  "pair.cramers_v_delta": "Cramér's V Δ",
  "pair.nmi_delta": "NMI Δ",
  "pair.contingency_tvd": "Contingency TV",
  "row.exact_match_rate": "Exact match",
  "row.exact_match_rate_nonkey": "Exact match (non-key)",
  "row.memorization_lift": "Memorization lift",
  "row.exposure_lift": "Exposure lift",
  "row.near_match_rate": "Near match",
  "row.near_match_lift": "Near-match lift",
  "row.internal_duplicate_excess": "Duplicate excess",
  "row.dcr_train_holdout_share": "DCR holdout share",
  "row.dcr_p5_ratio": "DCR p5 ratio",
  "row.nndr_p5_ratio": "NNDR p5 ratio",
  "row.density": "Density",
  "row.coverage": "Coverage",
  "row.null_pattern_tvd": "Null patterns",
  "table.detection_auc": "Detection AUC",
  "table.pmse_ratio": "pMSE ratio",
  "table.corr_rms_delta": "Corr. RMS Δ",
  "table.corr_max_delta": "Corr. max Δ",
  "table.row_count_ratio": "Row count",
  "table.pk_duplicate_rate": "PK duplicates",
  "table.identity_duplicate_rate": "Identity duplicates",
  "relationship.orphan_rate": "Orphan rate",
  "relationship.orphan_rate_source": "Source orphan rate",
  "relationship.fanout_tvd": "Fan-out TVD",
  "relationship.fanout_w1": "Fan-out W1",
  "relationship.fanout_mean_ratio": "Mean fan-out",
  "relationship.zero_child_share_delta": "Zero-child Δ",
  "relationship.cardinality_adherence": "Cardinality",
  "relationship.parent_coverage": "Parent coverage",
};

/** A compact label: the short header, else the catalogue title, else the raw id (an id newer than this build). */
export function metricShort(id: string): string {
  return SHORT[id] ?? metricMeta(id)?.title ?? id;
}

export function metricTitle(id: string): string {
  return metricMeta(id)?.title ?? id;
}

/**
 * Aggregates (`table.*_score`, every `model.*` id) never feed a roll-up (Ruling R11), are left
 * out of the headline counts (R43) and never appear as a column in the heatmap — the evaluator's
 * `is_aggregate`.
 */
export function isRollup(id: string): boolean {
  return isAggregateMetric(id);
}

/** The zero-tolerance integrity metrics (warn = fail = 0): what the key-failure badge counts (R39). */
export const ZERO_TOLERANCE_IDS: ReadonlySet<string> = zeroToleranceIds(catalogue);

/** The model-level roll-up id for a family ("overall" included). */
export function modelScoreId(family: string): string {
  return `model.${family}_score`;
}

export function tableScoreId(family: string): string {
  return `table.${family}_score`;
}
