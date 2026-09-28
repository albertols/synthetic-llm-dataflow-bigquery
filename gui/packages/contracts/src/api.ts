/**
 * The BFF's HTTP contract: every `/api/*` request and response, as zod.
 *
 * Rows are the generated BigQuery row schemas (`generated/schemas.ts`) so mock
 * and live data validate against the same types; the few derived fields
 * (parsed JSON strings, aligned comparisons, facets) are defined here. The web
 * app imports these as types only (`import type`), so zod never reaches the
 * shell chunk; the server and the tests validate with them.
 *
 * List parameters in query strings are comma-separated (`engine=b1_rag,b2_library`).
 * Every response from a BigQuery-backed route carries `x-data-source` and, in
 * bigquery mode, `x-bq-bytes-estimate` (the dry-run bytes of the queries it ran).
 *
 * Timestamps are `YYYY-MM-DDTHH:MM:SS.ffffffZ` (timestamps.ts): UTC, six fraction
 * digits, so string order is time order and a value sent back keeps its microseconds.
 *
 * Vocabulary fields (the generated `z.enum`s parsed from BigQuery column
 * descriptions) are tolerant on live data: a value the contract does not know yet
 * passes through as a plain string and the response carries `x-contract-warnings`
 * (count, then the first findings). Render an unknown vocabulary value as text; never
 * switch over a vocabulary without a default branch. Any other drift is a 502.
 */
import { z } from "zod";

import {
  evaluationDataHistoryRowSchema,
  evaluationMetricsRowSchema,
  evaluationProfilesRowSchema,
  evaluationRowFlagsRowSchema,
  freetextPoolsRowSchema,
  ragChunksRowSchema,
  sourceTableStatsRowSchema,
  validationRunsRowSchema,
} from "../generated/schemas";
import type { RelationshipModel } from "./relational";
import { profilerStatsSchema } from "./sourceStats";

// ---------------------------------------------------------------- helpers --

const isoTimestamp = z.iso.datetime({ offset: true });

/** "a,b,c" → ["a", "b", "c"]; an array passes through; empty → undefined. */
const csv = <T extends z.ZodType>(item: T) =>
  z.preprocess((value) => {
    if (value === undefined || value === null || value === "") return undefined;
    const list: unknown[] = Array.isArray(value) ? value : [value];
    const parts = list.flatMap((v) => (typeof v === "string" || typeof v === "number" ? String(v).split(",") : []));
    const trimmed = parts.map((p) => p.trim()).filter(Boolean);
    return trimmed.length ? trimmed : undefined;
  }, z.array(item).optional());

/** A filter bound: an ISO timestamp with offset, or a date (midnight UTC). */
const timeBound = z.union([isoTimestamp, z.iso.date()]).optional();

const optionalNumber = z.preprocess(
  (value) => (value === undefined || value === "" ? undefined : Number(value)),
  z.number().optional(),
);

export const HEADER_DATA_SOURCE = "x-data-source";
export const HEADER_BYTES_ESTIMATE = "x-bq-bytes-estimate";
export const HEADER_VECTOR_DIM = "x-vector-dim";
export const HEADER_VECTOR_COUNT = "x-vector-count";
/** "<n>; <finding>; <finding>…" — vocabulary values newer than the contract (live data only). */
export const HEADER_CONTRACT_WARNINGS = "x-contract-warnings";

export const dataSourceModeSchema = z.enum(["mock", "bigquery"]);
export type DataSourceMode = z.infer<typeof dataSourceModeSchema>;

export const apiErrorSchema = z.object({
  statusCode: z.int(),
  error: z.string(),
  message: z.string(),
  /** Which contract failed, for 502 contract errors on live data. */
  details: z.array(z.string()).optional(),
});
export type ApiError = z.infer<typeof apiErrorSchema>;

export function pageSchema<T extends z.ZodType>(item: T) {
  return z.object({ items: z.array(item), total: z.int().nonnegative(), offset: z.int(), limit: z.int() });
}
export type Page<T> = { items: T[]; total: number; offset: number; limit: number };

// ----------------------------------------------------------------- health --

export const healthSchema = z.object({
  status: z.literal("ok"),
  mode: dataSourceModeSchema,
  /** GCP project in bigquery mode; null in mock mode. */
  project: z.string().nullable(),
  location: z.string().nullable(),
  datasets: z.object({ quality: z.string(), rag: z.string() }),
  max_bytes_billed: z.int().positive(),
  catalogue_version: z.string(),
  /** sha256 of generated/manifest.json: which contract build this server runs. */
  contracts_digest: z.string(),
  server_version: z.string(),
  started_at: isoTimestamp,
});
export type Health = z.infer<typeof healthSchema>;

// ------------------------------------------------------------ evaluations --

/** One registry row as the `evaluation_latest` view returns it, without the two JSON snapshots. */
export const evaluationSummarySchema = evaluationDataHistoryRowSchema.omit({
  generation_params: true,
  evaluation_params: true,
});
export type EvaluationSummary = z.infer<typeof evaluationSummarySchema>;
export type EvaluationRecord = z.infer<typeof evaluationDataHistoryRowSchema>;
export type MetricRow = z.infer<typeof evaluationMetricsRowSchema>;
export type ProfileRow = z.infer<typeof evaluationProfilesRowSchema>;
export type RowFlag = z.infer<typeof evaluationRowFlagsRowSchema>;

export const evaluationSortKeys = [
  "evaluated_at",
  "overall_score",
  "fidelity_score",
  "privacy_score",
  "integrity_score",
  "diversity_score",
  "num_rows_requested",
  "status",
] as const;

/** Filters of `GET /api/evaluations` (and the evaluation filters of `/api/trend`). All optional; lists mean "any of". */
export const evaluationFilterSchema = z.object({
  /** Table names within the relationship model (`tables[].name`). */
  tables: csv(z.string()),
  engine: csv(z.string()),
  llm_model: csv(z.string()),
  embedder: csv(z.string()),
  retrieval: csv(z.string()),
  seed: csv(z.string()),
  similarity_min: optionalNumber,
  similarity_max: optionalNumber,
  reference_rows_limit: csv(z.coerce.number().int()),
  num_rows: csv(z.coerce.number().int()),
  source_stats_tier: csv(z.string()),
  profiler_version: csv(z.string()),
  env: csv(z.string()),
  status: csv(z.string()),
  trigger: csv(z.string()),
  runner: csv(z.string()),
  mode: csv(z.string()),
  evaluator_version: csv(z.string()),
  catalogue_version: csv(z.string()),
  relationship_model: csv(z.string()),
  /** evaluated_at ≥ from (ISO timestamp with offset, or a date = midnight UTC). */
  from: timeBound,
  /** evaluated_at < to. */
  to: timeBound,
  /** Substring match on evaluation_id, run_ids, base_run_id, generation_job_id / _name. */
  q: z.string().max(200).optional(),
  sort: z.enum(evaluationSortKeys).default("evaluated_at"),
  order: z.enum(["asc", "desc"]).default("desc"),
  offset: z.coerce.number().int().min(0).default(0),
  limit: z.coerce.number().int().min(1).max(500).default(50),
});
/** What a client sends (lists become comma-separated query params); the server parses it with `evaluationFilterSchema`. */
export interface EvaluationFilter {
  tables?: string[];
  engine?: string[];
  llm_model?: string[];
  embedder?: string[];
  retrieval?: string[];
  seed?: string[];
  similarity_min?: number;
  similarity_max?: number;
  reference_rows_limit?: number[];
  num_rows?: number[];
  source_stats_tier?: string[];
  profiler_version?: string[];
  env?: string[];
  status?: string[];
  trigger?: string[];
  runner?: string[];
  mode?: string[];
  evaluator_version?: string[];
  catalogue_version?: string[];
  relationship_model?: string[];
  /** ISO timestamp with offset (`…Z`, `…+02:00`) or a date: evaluated_at ≥ from. Anything else is a 400. */
  from?: string;
  /** evaluated_at < to. */
  to?: string;
  q?: string;
  sort?: EvaluationSortKey;
  order?: "asc" | "desc";
  offset?: number;
  /** 1–500, default 50. */
  limit?: number;
}
export type EvaluationSortKey = (typeof evaluationSortKeys)[number];
export type EvaluationFilterParsed = z.output<typeof evaluationFilterSchema>;

export const evaluationPageSchema = pageSchema(evaluationSummarySchema);

/** `GET /api/evaluations/:id` — the latest event, every event, and the evaluation's metric tables. */
export const evaluationDetailSchema = z.object({
  evaluation: evaluationDataHistoryRowSchema,
  /** Every registry event for this id, oldest first (RUNNING, then FINAL). */
  events: z.array(evaluationDataHistoryRowSchema),
  metrics: z.array(evaluationMetricsRowSchema),
  /**
   * Empty by default (`?profiles=none`): an evaluation carries hundreds of profile
   * payloads, so a detail page fetches `/api/evaluations/:id/profiles` per table,
   * column or kind when a drawer opens. `?profiles=all` embeds every one.
   */
  profiles: z.array(evaluationProfilesRowSchema),
  flags: z.array(evaluationRowFlagsRowSchema),
});
export type EvaluationDetail = z.infer<typeof evaluationDetailSchema>;

export const evaluationDetailQuerySchema = z.object({
  profiles: z.enum(["all", "none"]).default("none"),
});

/** `GET /api/evaluations/:id/profiles` — lazy profile loading for a drawer. */
export const profileQuerySchema = z.object({
  table: z.string().optional(),
  column: z.string().optional(),
  kind: csv(z.string()),
  side: csv(z.string()),
});
export interface ProfileQuery {
  table?: string;
  column?: string;
  kind?: string[];
  side?: string[];
}

// ------------------------------------------------------------------ trend --

export const trendQuerySchema = evaluationFilterSchema.omit({ sort: true, order: true, offset: true }).extend({
  metric_id: z.string().min(1),
  table: z.string().optional(),
  column: z.string().optional(),
  column_2: z.string().optional(),
  edge: z.string().optional(),
  limit: z.coerce.number().int().min(1).max(2000).default(500),
});
export type TrendQuery = Omit<EvaluationFilter, "sort" | "order" | "offset" | "limit"> & {
  metric_id: string;
  table?: string;
  column?: string;
  column_2?: string;
  edge?: string;
  /** 1–2,000, default 500: the newest points win. */
  limit?: number;
};
export type TrendQueryParsed = z.output<typeof trendQuerySchema>;

/**
 * One metric row of one evaluation, with the generation parameters to colour it by.
 * `/api/trend` returns the NEWEST `limit` points, ordered oldest first (ready to plot).
 */
export const trendPointSchema = z.object({
  evaluation_id: z.string(),
  evaluated_at: isoTimestamp,
  evaluation_status: evaluationDataHistoryRowSchema.shape.status,
  catalogue_version: z.string(),
  metric_id: z.string(),
  table_name: z.string(),
  column_name: z.string().nullable(),
  column_name_2: z.string().nullable(),
  edge: z.string().nullable(),
  status: evaluationMetricsRowSchema.shape.status,
  value: z.number().nullable(),
  score: z.number().nullable(),
  baseline_value: z.number().nullable(),
  noise_floor: z.number().nullable(),
  ci_low: z.number().nullable(),
  ci_high: z.number().nullable(),
  threshold_warn: z.number().nullable(),
  threshold_fail: z.number().nullable(),
  n_source: z.int().nullable(),
  n_synthetic: z.int().nullable(),
  engine: z.string().nullable(),
  llm_model_uri: z.string().nullable(),
  embedder_id: z.string().nullable(),
  retrieval_method: z.string().nullable(),
  seed: z.string().nullable(),
  similarity: z.number().nullable(),
  reference_rows_limit: z.int().nullable(),
  num_rows_requested: z.int().nullable(),
  source_stats_tier: z.string().nullable(),
});
export type TrendPoint = z.infer<typeof trendPointSchema>;

// ---------------------------------------------------------------- compare --

export const compareQuerySchema = z.object({
  ids: csv(z.string()).pipe(z.array(z.string()).min(1).max(12)),
});

export const metricCellSchema = z.object({
  value: z.number().nullable(),
  score: z.number().nullable(),
  status: evaluationMetricsRowSchema.shape.status,
  baseline_value: z.number().nullable(),
  noise_floor: z.number().nullable(),
  ci_low: z.number().nullable(),
  ci_high: z.number().nullable(),
  threshold_warn: z.number().nullable(),
  threshold_fail: z.number().nullable(),
  n_source: z.int().nullable(),
  n_synthetic: z.int().nullable(),
  encoding_plan_digest: z.string().nullable(),
});
export type MetricCell = z.infer<typeof metricCellSchema>;

/** One metric key across the compared evaluations; `cells[i]` belongs to `evaluations[i]` (null = absent there). */
export const comparedMetricSchema = z.object({
  /** "<metric_id>|<table>|<column>|<column_2>|<edge>" (empty segments for nulls). */
  key: z.string(),
  metric_id: z.string(),
  table_name: z.string(),
  column_name: z.string().nullable(),
  column_name_2: z.string().nullable(),
  edge: z.string().nullable(),
  level: evaluationMetricsRowSchema.shape.level,
  family: evaluationMetricsRowSchema.shape.family,
  value_kind: z.string().nullable(),
  cells: z.array(metricCellSchema.nullable()),
});
export type ComparedMetric = z.infer<typeof comparedMetricSchema>;

/** A generation or evaluation parameter across the compared evaluations (typed columns and flattened JSON snapshots). */
export const paramDiffSchema = z.object({
  /** "engine", "generation_params.pool_seed_strategy", "evaluation_params.mode" … */
  path: z.string(),
  values: z.array(z.json()),
  differs: z.boolean(),
});
export type ParamDiff = z.infer<typeof paramDiffSchema>;

export const comparisonSchema = z.object({
  evaluations: z.array(evaluationSummarySchema),
  /** Requested ids that do not exist. */
  missing: z.array(z.string()),
  metrics: z.array(comparedMetricSchema),
  params: z.array(paramDiffSchema),
  comparability: z.object({
    catalogue_version_same: z.boolean(),
    evaluator_version_same: z.boolean(),
    /** Per table: each evaluation's encoding_plan_digest (null when that evaluation lacks the table). */
    encoding_plans: z.array(
      z.object({ table_name: z.string(), digests: z.array(z.string().nullable()), same: z.boolean() }),
    ),
    /** Human-readable reasons the numbers are not directly comparable; empty when they are. */
    not_comparable: z.array(z.string()),
  }),
});
export type Comparison = z.infer<typeof comparisonSchema>;

// ------------------------------------------------------------ runs + dlq --

export const runFilterSchema = z.object({
  run_ids: csv(z.string()),
  /** A launch: its per-table rows are `<base>-NN-<table>` (a one-table launch writes `<base>` itself). */
  base_run_id: z.string().optional(),
  landing_table: csv(z.string()),
  engine: csv(z.string()),
  status: csv(z.string()),
  env: csv(z.string()),
  /** created_at ≥ from (ISO timestamp with offset, or a date). */
  from: timeBound,
  to: timeBound,
  limit: z.coerce.number().int().min(1).max(1000).default(200),
});
export interface RunFilter {
  run_ids?: string[];
  base_run_id?: string;
  landing_table?: string[];
  engine?: string[];
  status?: string[];
  env?: string[];
  from?: string;
  to?: string;
  /** 1–1,000, default 200. */
  limit?: number;
}
export type RunFilterParsed = z.output<typeof runFilterSchema>;

/** A validation_runs row plus `dlq_by_rule` parsed (the column is a JSON string). Newest first. */
export const validationRunSchema = validationRunsRowSchema.extend({
  dlq_by_rule_map: z.record(z.string(), z.int().nonnegative()),
});
export type ValidationRun = z.infer<typeof validationRunSchema>;

export const dlqQuerySchema = z.object({
  run_ids: csv(z.string()).pipe(z.array(z.string()).min(1).max(100)),
});

/** DLQ rows grouped by (run, rule, error type, step). */
export const dlqSummarySchema = z.object({
  run_id: z.string(),
  rule_id: z.string().nullable(),
  error_type: z.string().nullable(),
  pipeline_step: z.string().nullable(),
  stage: z.string().nullable(),
  count: z.int().nonnegative(),
  first_seen: isoTimestamp.nullable(),
  last_seen: isoTimestamp.nullable(),
  /** One example, with its JSON strings parsed (null when unparsable). */
  sample: z.object({ raw_record: z.json().nullable(), error_detail: z.json().nullable() }).nullable(),
  /** config/thresholds.yml declares the rule BLOCKER (knob blocker_rules_declared). */
  blocker_declared: z.boolean(),
  /** The run gate counts it (BLOCKER_RULE_IDS, knob blocker_rule_ids). Differs for fk.orphan: see annotations. */
  blocker_counted: z.boolean(),
});
export type DlqSummary = z.infer<typeof dlqSummarySchema>;

// ----------------------------------------------------------- source stats --

/**
 * Which snapshots to return. `snapshot` (keys from `snapshots[].key`) wins; else
 * `digest` and/or `tier` narrow the candidates and the newest match per tier is
 * returned; with neither, the newest snapshot of each tier (sample vs exact).
 */
export const sourceStatsQuerySchema = z.object({
  /** table_fqn, or the bare table name when it is unambiguous. */
  table: z.string().min(1),
  tier: z.enum(["sample", "exact"]).optional(),
  digest: z.string().optional(),
  snapshot: csv(z.string()).pipe(z.array(z.string()).max(8).optional()),
});
export interface SourceStatsQuery {
  /** table_fqn, or the bare table name when unambiguous. */
  table: string;
  tier?: "sample" | "exact";
  digest?: string;
  /** Snapshot keys (`snapshots[].key`), at most 8. */
  snapshot?: string[];
}

export const sourceStatsColumnSchema = sourceTableStatsRowSchema.extend({
  /** The snapshot this row belongs to (`snapshots[].key`). */
  snapshot_key: z.string(),
  /** `stats_tier` with NULL read as "sample" (legacy rows). */
  tier: z.enum(["sample", "exact"]),
  /** `stats` parsed; null when the string is not a profiler entry. */
  stats_parsed: profilerStatsSchema.nullable(),
});
export type SourceStatsColumn = z.infer<typeof sourceStatsColumnSchema>;

/** One profiler snapshot: every row sharing (reference_digest, tier, profiler_version, run_id). */
export const sourceStatsSnapshotSchema = z.object({
  /** `<reference_digest>|<tier>|<profiler_version>|<run_id>` (sourceStats.ts `snapshotKey`). */
  key: z.string(),
  reference_digest: z.string(),
  run_id: z.string(),
  /** NULL in the table reads as "sample". */
  tier: z.enum(["sample", "exact"]),
  /** The row had NULL `stats_tier` (written before the exact tier existed). */
  legacy_tier: z.boolean(),
  profiler_version: z.string().nullable(),
  sample_rows: z.int().nullable(),
  computed_at: isoTimestamp,
  columns: z.int().nonnegative(),
});
export type SourceStatsSnapshot = z.infer<typeof sourceStatsSnapshotSchema>;

/**
 * One table's stats. A reference digest can carry both tiers and several profiler
 * versions, so snapshots are keyed by (digest, tier, profiler_version, run_id);
 * `columns` holds the rows of the `selected` snapshots, told apart by `snapshot_key`.
 */
export const sourceStatsSchema = z.object({
  table_fqn: z.string(),
  tiers_available: z.array(z.enum(["sample", "exact"])),
  /** Every snapshot of this table, newest first. */
  snapshots: z.array(sourceStatsSnapshotSchema),
  /** Keys of the snapshots whose rows are in `columns`. */
  selected: z.array(z.string()),
  columns: z.array(sourceStatsColumnSchema),
});
export type SourceStats = z.infer<typeof sourceStatsSchema>;

// -------------------------------------------------------------------- RAG --

/**
 * At most 3,000 chunks per response: 3,000 × 384-d Float32 is 4.6 MB of vectors,
 * inside the 5 MB budget for the vector payload (chunk text and metadata come on
 * top; a wider embedder needs a smaller `limit`). Narrow by `source_fqn` /
 * `column` beyond that.
 */
export const RAG_CHUNKS_MAX = 3000;

export const ragChunksQuerySchema = z.object({
  digest: z.string().min(1),
  kind: ragChunksRowSchema.shape.chunk_kind,
  /** embedder_id, optionally "<id>/<version>". */
  embedder: z.string().min(1),
  limit: z.coerce.number().int().min(1).max(RAG_CHUNKS_MAX).default(1024),
  source_fqn: z.string().optional(),
  /** free_text_col only: the column (metadata.column). */
  column: z.string().optional(),
});
export interface RagChunksQuery {
  digest: string;
  kind: "row_doc" | "free_text_col";
  /** embedder_id, optionally "<id>/<version>". */
  embedder: string;
  /** 1–3,000 (`RAG_CHUNKS_MAX`), default 1,024. */
  limit?: number;
  source_fqn?: string;
  column?: string;
}

/**
 * Per-chunk metadata in the binary envelope (see vectors.ts); `embedding` travels as
 * Float32. `chunk_text` is the operator's own governed reference data, shown to the
 * operator on a loopback-only server; `source_pk` (a source row's key) is never sent.
 */
export const chunkMetaSchema = z.object({
  chunk_id: z.string(),
  source_fqn: z.string(),
  chunk_kind: ragChunksRowSchema.shape.chunk_kind,
  chunk_index: z.int(),
  chunk_text: z.string(),
  /** metadata.column for free_text_col chunks. */
  column: z.string().nullable(),
  row_digest: z.string(),
  embedder_id: z.string(),
  embedder_version: z.string(),
});
export type ChunkMeta = z.infer<typeof chunkMetaSchema>;

export const freetextPoolsQuerySchema = z.object({
  digest: z.string().min(1),
  model_uri: z.string().optional(),
  column: z.string().optional(),
});
export interface FreetextPoolsQuery {
  digest: string;
  model_uri?: string;
  column?: string;
}

export const freetextPoolSchema = freetextPoolsRowSchema.extend({
  /** values.length (the pool is deduped). */
  distinct: z.int().nonnegative(),
});
export type FreetextPool = z.infer<typeof freetextPoolSchema>;

// ----------------------------------------------------------------- facets --

export const ragSetSchema = z.object({
  source_fqn: z.string(),
  reference_digest: z.string(),
  embedder_id: z.string(),
  embedder_version: z.string(),
  row_docs: z.int().nonnegative(),
  value_chunks: z.int().nonnegative(),
  /** Free-text columns with value chunks. */
  columns: z.array(z.string()),
  dim: z.int().positive().nullable(),
  created_at: isoTimestamp.nullable(),
});
export type RagSet = z.infer<typeof ragSetSchema>;

/** Filter options and headline counts for every tab. */
export const facetsSchema = z.object({
  counts: z.object({
    evaluations: z.int().nonnegative(),
    runs: z.int().nonnegative(),
    tables: z.int().nonnegative(),
    relationship_models: z.int().nonnegative(),
    metrics: z.int().nonnegative(),
  }),
  latest: z
    .object({
      evaluation_id: z.string(),
      evaluated_at: isoTimestamp,
      status: evaluationDataHistoryRowSchema.shape.status,
      overall_score: z.number().nullable(),
    })
    .nullable(),
  date_range: z.object({ min: isoTimestamp, max: isoTimestamp }).nullable(),
  tables: z.array(z.string()),
  engines: z.array(z.string()),
  llm_models: z.array(z.string()),
  embedders: z.array(z.string()),
  retrieval_methods: z.array(z.string()),
  seeds: z.array(z.string()),
  similarities: z.array(z.number()),
  reference_rows_limits: z.array(z.int()),
  num_rows: z.array(z.int()),
  source_stats_tiers: z.array(z.string()),
  profiler_versions: z.array(z.string()),
  envs: z.array(z.string()),
  statuses: z.array(z.string()),
  triggers: z.array(z.string()),
  runners: z.array(z.string()),
  modes: z.array(z.string()),
  evaluator_versions: z.array(z.string()),
  catalogue_versions: z.array(z.string()),
  relationship_models: z.array(z.string()),
  /** Metric ids present in evaluation_metrics. */
  metric_ids: z.array(z.string()),
  source_tables: z.array(
    z.object({
      table_fqn: z.string(),
      tiers: z.array(z.string()),
      latest_computed_at: isoTimestamp,
      columns: z.int().nonnegative(),
    }),
  ),
  rag: z.array(ragSetSchema),
  /** Free-text pool sets; `table_fqn` is the source table the digest was sampled from (null if no run or stats row names it). */
  pools: z.array(
    z.object({
      reference_digest: z.string(),
      table_fqn: z.string().nullable(),
      model_uri: z.string(),
      columns: z.array(z.string()),
    }),
  ),
});
export type Facets = z.infer<typeof facetsSchema>;

// ---------------------------------------------------------- relationships --

const relationEdgeSchema = z.object({
  cols: z.array(z.string()),
  ref: z.string(),
  ref_cols: z.array(z.string()),
  enforced: z.boolean(),
  drives: z.boolean(),
  external: z.boolean(),
  role: z.enum(["driving", "implied", "conditional", "independent", "external", "documented", "disabled"]),
  drawn_cols: z.array(z.string()),
  note: z.string(),
});

export const relationshipModelSchema = z.object({
  model: z.string(),
  description: z.string(),
  source: z.string(),
  sha12: z.string(),
  generation_order: z.array(z.string()),
  tables: z.array(
    z.object({
      name: z.string(),
      pk: z.array(z.string()),
      identity: z.array(z.string()),
      enabled: z.boolean(),
      note: z.string(),
      fk: z.array(relationEdgeSchema),
    }),
  ),
}) satisfies z.ZodType<RelationshipModel>;

/**
 * `GET /api/relationships` — the relationship models a registry row's
 * `relationship_model` can resolve to: the committed sample models
 * (generated/relationships.json, `origin: "committed_example"`) and, in mock mode,
 * the mock's invented `thelook_demo` (`origin: "mock"`). Real models are gitignored
 * or on GCS and never served; when a row's model is not listed, rebuild the graph
 * from the metric rows' `edge` labels (`parseEdge`).
 */
export const relationshipsResponseSchema = z.object({
  models: z.array(relationshipModelSchema.extend({ origin: z.enum(["committed_example", "mock"]) })),
});
export type RelationshipsResponse = z.infer<typeof relationshipsResponseSchema>;

// ------------------------------------------------------- knobs, catalogue --

export const catalogueResponseSchema = z.object({
  version: z.string(),
  levels: z.array(z.string()),
  families: z.array(z.string()),
  metrics: z.array(z.looseObject({ id: z.string(), title: z.string(), level: z.string(), family: z.string() })),
});

// Compile-time guard: each client interface names exactly the keys its zod schema parses.
type SameKeys<A, B> = [Exclude<keyof A, keyof B>, Exclude<keyof B, keyof A>] extends [never, never] ? true : false;
export const clientTypesMatchSchemas: [
  SameKeys<EvaluationFilter, z.output<typeof evaluationFilterSchema>>,
  SameKeys<ProfileQuery, z.output<typeof profileQuerySchema>>,
  SameKeys<TrendQuery, z.output<typeof trendQuerySchema>>,
  SameKeys<RunFilter, z.output<typeof runFilterSchema>>,
  SameKeys<SourceStatsQuery, z.output<typeof sourceStatsQuerySchema>>,
  SameKeys<RagChunksQuery, z.output<typeof ragChunksQuerySchema>>,
  SameKeys<FreetextPoolsQuery, z.output<typeof freetextPoolsQuerySchema>>,
] = [true, true, true, true, true, true, true];
