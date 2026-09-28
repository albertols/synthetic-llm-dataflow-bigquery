/**
 * The BFF's named, parameterized, read-only queries — the only SQL the GUI
 * ever runs. The browser sends filter values; routes validate them with zod;
 * values reach BigQuery only as query parameters (`@name`). The only text
 * interpolated into SQL is the server's own configuration (project and
 * dataset names, validated at startup) and ORDER BY columns picked from a
 * fixed map. Every query is SELECT-only and runs after a dry run under
 * `maximumBytesBilled` (see providers/bigquery.ts).
 *
 * Reads go through `evaluation_latest` (views.sql: the last registry event per
 * evaluation_id) unless the events themselves are wanted.
 */
import { bqTables, type BqField, type BqTableName, type evaluationSortKeys } from "@synthetic-platform/contracts";

export interface Datasets {
  /** `project.synthetic_data_quality` */
  quality: string;
  /** `project.synthetic_rag` */
  rag: string;
}

export type ParamType = string | [string];

export interface NamedQuery {
  name: string;
  description: string;
  /** The SQL, given the datasets and (for list queries) the validated sort. */
  sql(d: Datasets, options?: { sort?: SortKey; order?: "asc" | "desc" }): string;
  /** Every parameter's BigQuery type (a one-element array = ARRAY<type>), so NULLs and empty arrays bind. */
  types: Record<string, ParamType>;
  /** The result rows' field tree (for normalizing BigQuery values before zod). */
  fields: readonly BqField[];
}

export type SortKey = (typeof evaluationSortKeys)[number];

const SORT_COLUMNS: Record<SortKey, string> = {
  evaluated_at: "evaluated_at",
  overall_score: "overall_score",
  fidelity_score: "fidelity_score",
  privacy_score: "privacy_score",
  integrity_score: "integrity_score",
  diversity_score: "diversity_score",
  num_rows_requested: "num_rows_requested",
  status: "status",
};

const fields = (table: BqTableName) => bqTables[table].fields as readonly BqField[];
const omit = (table: BqTableName, names: string[]) => fields(table).filter((f) => !names.includes(f.name));
const pick = (table: BqTableName, names: string[]) => names.map((n) => fields(table).find((f) => f.name === n)!);
const field = (name: string, type: string, mode = "NULLABLE"): BqField => ({ name, type, mode });

const SUMMARY_FIELDS = [
  ...omit("evaluation_data_history", ["generation_params", "evaluation_params"]),
  field("total_rows", "INT64"),
];

/** The evaluation filters (list and trend), on a table alias. Every value is a parameter. */
export const EVALUATION_FILTER_TYPES: Record<string, ParamType> = {
  tables: ["STRING"],
  engine: ["STRING"],
  llm_model: ["STRING"],
  embedder: ["STRING"],
  retrieval: ["STRING"],
  seed: ["STRING"],
  similarity_min: "FLOAT64",
  similarity_max: "FLOAT64",
  reference_rows_limit: ["INT64"],
  num_rows: ["INT64"],
  source_stats_tier: ["STRING"],
  profiler_version: ["STRING"],
  env: ["STRING"],
  status: ["STRING"],
  trigger: ["STRING"],
  runner: ["STRING"],
  mode: ["STRING"],
  evaluator_version: ["STRING"],
  catalogue_version: ["STRING"],
  relationship_model: ["STRING"],
  from: "STRING",
  to: "STRING",
  q: "STRING",
};

function evaluationWhere(a: string): string {
  const inList = (param: string, column: string) =>
    `(ARRAY_LENGTH(@${param}) = 0 OR ${a}.${column} IN UNNEST(@${param}))`;
  return [
    `(ARRAY_LENGTH(@tables) = 0 OR EXISTS (SELECT 1 FROM UNNEST(${a}.tables) AS t WHERE t.name IN UNNEST(@tables)))`,
    inList("engine", "engine"),
    inList("llm_model", "llm_model_uri"),
    inList("embedder", "embedder_id"),
    inList("retrieval", "retrieval_method"),
    inList("seed", "seed"),
    `(@similarity_min IS NULL OR ${a}.similarity >= @similarity_min)`,
    `(@similarity_max IS NULL OR ${a}.similarity <= @similarity_max)`,
    inList("reference_rows_limit", "reference_rows_limit"),
    inList("num_rows", "num_rows_requested"),
    inList("source_stats_tier", "source_stats_tier"),
    inList("profiler_version", "profiler_version"),
    inList("env", "env"),
    inList("status", "status"),
    inList("trigger", "`trigger`"),
    inList("runner", "runner"),
    inList("mode", "`mode`"),
    inList("evaluator_version", "evaluator_version"),
    inList("catalogue_version", "catalogue_version"),
    inList("relationship_model", "relationship_model"),
    `(@from IS NULL OR ${a}.evaluated_at >= TIMESTAMP(@from))`,
    `(@to IS NULL OR ${a}.evaluated_at < TIMESTAMP(@to))`,
    `(@q IS NULL OR STRPOS(LOWER(${a}.evaluation_id), LOWER(@q)) > 0
      OR STRPOS(LOWER(IFNULL(${a}.base_run_id, '')), LOWER(@q)) > 0
      OR STRPOS(LOWER(IFNULL(${a}.generation_job_id, '')), LOWER(@q)) > 0
      OR STRPOS(LOWER(IFNULL(${a}.generation_job_name, '')), LOWER(@q)) > 0
      OR EXISTS (SELECT 1 FROM UNNEST(${a}.run_ids) AS r WHERE STRPOS(LOWER(r), LOWER(@q)) > 0))`,
  ].join("\n  AND ");
}

const TREND_FIELDS: BqField[] = [
  field("evaluation_id", "STRING", "REQUIRED"),
  field("evaluated_at", "TIMESTAMP", "REQUIRED"),
  field("evaluation_status", "STRING", "REQUIRED"),
  field("catalogue_version", "STRING", "REQUIRED"),
  ...pick("evaluation_metrics", [
    "metric_id",
    "table_name",
    "column_name",
    "column_name_2",
    "edge",
    "status",
    "value",
    "score",
    "baseline_value",
    "noise_floor",
    "ci_low",
    "ci_high",
    "threshold_warn",
    "threshold_fail",
    "n_source",
    "n_synthetic",
  ]),
  ...pick("evaluation_data_history", [
    "engine",
    "llm_model_uri",
    "embedder_id",
    "retrieval_method",
    "seed",
    "similarity",
    "reference_rows_limit",
    "num_rows_requested",
    "source_stats_tier",
  ]),
];

export const QUERIES = {
  "evaluations.list": {
    name: "evaluations.list",
    description: "evaluation_latest rows (without the JSON snapshots), filtered, sorted, one page, with the total.",
    types: { ...EVALUATION_FILTER_TYPES, limit: "INT64", offset: "INT64" },
    fields: SUMMARY_FIELDS,
    sql: (d, o = {}) => `SELECT * EXCEPT (generation_params, evaluation_params), COUNT(*) OVER () AS total_rows
FROM \`${d.quality}.evaluation_latest\` AS e
WHERE ${evaluationWhere("e")}
ORDER BY ${SORT_COLUMNS[o.sort ?? "evaluated_at"]} ${o.order === "asc" ? "ASC" : "DESC"} NULLS LAST, evaluation_id
LIMIT @limit OFFSET @offset`,
  },
  "evaluations.events": {
    name: "evaluations.events",
    description: "Every registry event of one evaluation, oldest first.",
    types: { id: "STRING" },
    fields: fields("evaluation_data_history"),
    sql: (d) => `SELECT * FROM \`${d.quality}.evaluation_data_history\`
WHERE evaluation_id = @id
ORDER BY recorded_at`,
  },
  "evaluations.latestByIds": {
    name: "evaluations.latestByIds",
    description: "The latest registry row of each requested evaluation (compare).",
    types: { ids: ["STRING"] },
    fields: fields("evaluation_data_history"),
    sql: (d) => `SELECT * FROM \`${d.quality}.evaluation_latest\`
WHERE evaluation_id IN UNNEST(@ids)`,
  },
  "evaluations.slim": {
    name: "evaluations.slim",
    description: "evaluation_latest without the JSON snapshots, for facets.",
    types: {},
    fields: omit("evaluation_data_history", ["generation_params", "evaluation_params"]),
    sql: (d) => `SELECT * EXCEPT (generation_params, evaluation_params) FROM \`${d.quality}.evaluation_latest\``,
  },
  "metrics.byEvaluation": {
    name: "metrics.byEvaluation",
    description: "One evaluation's metric rows (pruned to its evaluated_at partition).",
    types: { id: "STRING", evaluated_at: "STRING" },
    fields: fields("evaluation_metrics"),
    sql: (d) => `SELECT * FROM \`${d.quality}.evaluation_metrics\`
WHERE evaluation_id = @id AND evaluated_at = TIMESTAMP(@evaluated_at)`,
  },
  "metrics.byEvaluations": {
    name: "metrics.byEvaluations",
    description: "The metric rows of several evaluations (compare).",
    types: { ids: ["STRING"], evaluated_at: ["STRING"] },
    fields: fields("evaluation_metrics"),
    sql: (d) => `SELECT * FROM \`${d.quality}.evaluation_metrics\`
WHERE evaluation_id IN UNNEST(@ids)
  AND evaluated_at IN (SELECT TIMESTAMP(x) FROM UNNEST(@evaluated_at) AS x)`,
  },
  "metrics.ids": {
    name: "metrics.ids",
    description: "The distinct metric ids written in the last 400 days (facets).",
    types: {},
    fields: [field("metric_id", "STRING", "REQUIRED"), field("rows", "INT64")],
    sql: (d) => `SELECT metric_id, COUNT(*) AS rows FROM \`${d.quality}.evaluation_metrics\`
WHERE evaluated_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 400 DAY)
GROUP BY metric_id`,
  },
  "profiles.byEvaluation": {
    name: "profiles.byEvaluation",
    description: "One evaluation's profiles, optionally one table, column, kinds and sides.",
    types: {
      id: "STRING",
      evaluated_at: "STRING",
      table: "STRING",
      column: "STRING",
      kinds: ["STRING"],
      sides: ["STRING"],
    },
    fields: fields("evaluation_profiles"),
    sql: (d) => `SELECT * FROM \`${d.quality}.evaluation_profiles\`
WHERE evaluation_id = @id AND evaluated_at = TIMESTAMP(@evaluated_at)
  AND (@table IS NULL OR table_name = @table)
  AND (@column IS NULL OR column_name = @column)
  AND (ARRAY_LENGTH(@kinds) = 0 OR profile_kind IN UNNEST(@kinds))
  AND (ARRAY_LENGTH(@sides) = 0 OR side IN UNNEST(@sides))`,
  },
  "flags.byEvaluation": {
    name: "flags.byEvaluation",
    description: "One evaluation's flagged rows (keys only; source keys hashed by default).",
    types: { id: "STRING", evaluated_at: "STRING" },
    fields: fields("evaluation_row_flags"),
    sql: (d) => `SELECT * FROM \`${d.quality}.evaluation_row_flags\`
WHERE evaluation_id = @id AND evaluated_at = TIMESTAMP(@evaluated_at)
ORDER BY table_name, \`check\`, rank`,
  },
  trend: {
    name: "trend",
    description:
      "One metric across evaluations (optionally one table / column / pair / edge), with the parameters to colour by.",
    types: {
      ...EVALUATION_FILTER_TYPES,
      metric_id: "STRING",
      table: "STRING",
      column: "STRING",
      column_2: "STRING",
      edge: "STRING",
      limit: "INT64",
    },
    fields: TREND_FIELDS,
    sql: (d) => `SELECT e.evaluation_id, e.evaluated_at, e.status AS evaluation_status, e.catalogue_version,
  m.metric_id, m.table_name, m.column_name, m.column_name_2, m.edge, m.status, m.value, m.score,
  m.baseline_value, m.noise_floor, m.ci_low, m.ci_high, m.threshold_warn, m.threshold_fail, m.n_source, m.n_synthetic,
  e.engine, e.llm_model_uri, e.embedder_id, e.retrieval_method, e.seed, e.similarity,
  e.reference_rows_limit, e.num_rows_requested, e.source_stats_tier
FROM \`${d.quality}.evaluation_metrics\` AS m
JOIN \`${d.quality}.evaluation_latest\` AS e ON e.evaluation_id = m.evaluation_id
WHERE m.metric_id = @metric_id
  AND (@table IS NULL OR m.table_name = @table)
  AND (@column IS NULL OR m.column_name = @column)
  AND (@column_2 IS NULL OR m.column_name_2 = @column_2)
  AND (@edge IS NULL OR m.edge = @edge)
  AND ${evaluationWhere("e")}
ORDER BY e.evaluated_at, m.table_name
LIMIT @limit`,
  },
  "runs.list": {
    name: "runs.list",
    description: "validation_runs, newest first, filtered.",
    types: {
      run_ids: ["STRING"],
      base_run_id: "STRING",
      landing_table: ["STRING"],
      engine: ["STRING"],
      status: ["STRING"],
      env: ["STRING"],
      from: "STRING",
      to: "STRING",
      limit: "INT64",
    },
    fields: fields("validation_runs"),
    sql: (d) => `SELECT * FROM \`${d.quality}.validation_runs\`
WHERE (ARRAY_LENGTH(@run_ids) = 0 OR run_id IN UNNEST(@run_ids))
  AND (@base_run_id IS NULL OR STARTS_WITH(run_id, CONCAT(@base_run_id, '-')))
  AND (ARRAY_LENGTH(@landing_table) = 0 OR landing_table IN UNNEST(@landing_table))
  AND (ARRAY_LENGTH(@engine) = 0 OR engine IN UNNEST(@engine))
  AND (ARRAY_LENGTH(@status) = 0 OR status IN UNNEST(@status))
  AND (ARRAY_LENGTH(@env) = 0 OR env IN UNNEST(@env))
  AND (@from IS NULL OR created_at >= TIMESTAMP(@from))
  AND (@to IS NULL OR created_at < TIMESTAMP(@to))
ORDER BY created_at DESC, run_id
LIMIT @limit`,
  },
  "runs.count": {
    name: "runs.count",
    description: "How many validation_runs rows exist (facets).",
    types: {},
    fields: [field("runs", "INT64", "REQUIRED")],
    sql: (d) => `SELECT COUNT(*) AS runs FROM \`${d.quality}.validation_runs\``,
  },
  "dlq.summary": {
    name: "dlq.summary",
    description: "DLQ rows grouped by run, rule, error type, step and stage, with one example.",
    types: { run_ids: ["STRING"] },
    fields: [
      field("run_id", "STRING", "REQUIRED"),
      field("rule_id", "STRING"),
      field("error_type", "STRING"),
      field("pipeline_step", "STRING"),
      field("stage", "STRING"),
      field("count", "INT64", "REQUIRED"),
      field("first_seen", "TIMESTAMP"),
      field("last_seen", "TIMESTAMP"),
      field("raw_record", "STRING"),
      field("error_detail", "STRING"),
    ],
    sql: (d) => `SELECT run_id, rule_id, error_type, pipeline_step, stage, COUNT(*) AS count,
  MIN(dlq_inserted_at) AS first_seen, MAX(dlq_inserted_at) AS last_seen,
  ANY_VALUE(raw_record) AS raw_record, ANY_VALUE(error_detail) AS error_detail
FROM \`${d.quality}.dlq\`
WHERE run_id IN UNNEST(@run_ids)
GROUP BY run_id, rule_id, error_type, pipeline_step, stage`,
  },
  "sourceStats.tables": {
    name: "sourceStats.tables",
    description: "Every profiled source table with its tiers, latest snapshot time and column count (facets).",
    types: {},
    fields: [
      field("table_fqn", "STRING", "REQUIRED"),
      { name: "tiers", type: "STRING", mode: "REPEATED" },
      field("latest_computed_at", "TIMESTAMP", "REQUIRED"),
      field("columns", "INT64", "REQUIRED"),
    ],
    sql: (d) => `SELECT table_fqn, ARRAY_AGG(DISTINCT IFNULL(stats_tier, 'sample')) AS tiers,
  MAX(computed_at) AS latest_computed_at, COUNT(DISTINCT \`column\`) AS columns
FROM \`${d.rag}.source_table_stats\`
GROUP BY table_fqn`,
  },
  "sourceStats.byTable": {
    name: "sourceStats.byTable",
    description: "Every stats row of one source table (all snapshots; the provider selects).",
    types: { table_fqn: "STRING" },
    fields: fields("source_table_stats"),
    sql: (d) => `SELECT * FROM \`${d.rag}.source_table_stats\`
WHERE table_fqn = @table_fqn`,
  },
  "rag.sets": {
    name: "rag.sets",
    description:
      "Chunk sets per (source, reference digest, embedder) with their sizes (facets; never reads embeddings).",
    types: {},
    fields: [
      field("source_fqn", "STRING", "REQUIRED"),
      field("reference_digest", "STRING", "REQUIRED"),
      field("embedder_id", "STRING", "REQUIRED"),
      field("embedder_version", "STRING", "REQUIRED"),
      field("row_docs", "INT64", "REQUIRED"),
      field("value_chunks", "INT64", "REQUIRED"),
      { name: "columns", type: "STRING", mode: "REPEATED" },
      field("created_at", "TIMESTAMP"),
    ],
    sql: (d) => `SELECT source_fqn, reference_digest, embedder_id, embedder_version,
  COUNT(DISTINCT IF(chunk_kind = 'row_doc', chunk_id, NULL)) AS row_docs,
  COUNT(DISTINCT IF(chunk_kind = 'free_text_col', chunk_id, NULL)) AS value_chunks,
  ARRAY_AGG(DISTINCT JSON_VALUE(metadata, '$.column') IGNORE NULLS) AS columns,
  MAX(created_at) AS created_at
FROM \`${d.rag}.rag_chunks\`
GROUP BY source_fqn, reference_digest, embedder_id, embedder_version`,
  },
  "rag.chunks": {
    name: "rag.chunks",
    description: "Chunks of one set and kind (deduplicated by chunk_id), with their embeddings.",
    types: {
      digest: "STRING",
      kind: "STRING",
      embedder: "STRING",
      version: "STRING",
      source_fqn: "STRING",
      column: "STRING",
      limit: "INT64",
    },
    fields: fields("rag_chunks"),
    sql: (d) => `SELECT * FROM \`${d.rag}.rag_chunks\`
WHERE reference_digest = @digest AND chunk_kind = @kind AND embedder_id = @embedder
  AND (@version IS NULL OR embedder_version = @version)
  AND (@source_fqn IS NULL OR source_fqn = @source_fqn)
  AND (@column IS NULL OR JSON_VALUE(metadata, '$.column') = @column)
QUALIFY ROW_NUMBER() OVER (PARTITION BY chunk_id ORDER BY created_at DESC) = 1
ORDER BY source_fqn, chunk_kind, chunk_id
LIMIT @limit`,
  },
  "rag.pools": {
    name: "rag.pools",
    description: "Free-text pools of one reference digest, optionally one model and column.",
    types: { digest: "STRING", model_uri: "STRING", column: "STRING" },
    fields: fields("freetext_pools"),
    sql: (d) => `SELECT reference_digest, model_uri, \`column\`, target, \`values\`, stagnated, attempts
FROM \`${d.rag}.freetext_pools\`
WHERE reference_digest = @digest
  AND (@model_uri IS NULL OR model_uri = @model_uri)
  AND (@column IS NULL OR \`column\` = @column)
ORDER BY model_uri, \`column\``,
  },
  "rag.poolSets": {
    name: "rag.poolSets",
    description: "Which (reference digest, model) pairs have pools, and for which columns (facets).",
    types: {},
    fields: [
      field("reference_digest", "STRING", "REQUIRED"),
      field("model_uri", "STRING", "REQUIRED"),
      { name: "columns", type: "STRING", mode: "REPEATED" },
    ],
    sql: (d) => `SELECT reference_digest, model_uri, ARRAY_AGG(DISTINCT \`column\` ORDER BY \`column\`) AS columns
FROM \`${d.rag}.freetext_pools\`
GROUP BY reference_digest, model_uri`,
  },
} satisfies Record<string, NamedQuery>;

export type QueryName = keyof typeof QUERIES;

/** A query's SQL must be read-only: one statement, SELECT (or WITH) first, no DML/DDL keywords. */
export function assertReadOnly(sql: string): void {
  const text = sql.replace(/'(?:[^'\\]|\\.)*'/g, "''").trim();
  if (!/^(SELECT|WITH)\b/i.test(text)) throw new Error("named queries must start with SELECT or WITH");
  if (/;\s*\S/.test(text)) throw new Error("named queries must be a single statement");
  if (/\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|CALL|EXECUTE|EXPORT|LOAD)\b/i.test(text))
    throw new Error("named queries must not modify anything");
}
