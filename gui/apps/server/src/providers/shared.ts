/**
 * Logic both providers share, so mock and BigQuery answers are built the same
 * way from the same row shapes: registry "latest" semantics, filters, facets,
 * comparisons, trend points, DLQ summaries and source-stats assembly.
 */
import {
  knobs,
  profilerStatsSchema,
  type ComparedMetric,
  type Comparison,
  type DlqSummary,
  type EvaluationFilterParsed,
  type EvaluationRecord,
  type EvaluationSummary,
  type Facets,
  type MetricCell,
  type MetricRow,
  type ParamDiff,
  type RagSet,
  type SourceStats,
  type SourceTableStatsRow,
  type TrendPoint,
} from "@synthetic-platform/contracts";

/** `evaluation_latest`: the last event per evaluation_id (by recorded_at). */
export function latestPerEvaluation<T extends { evaluation_id: string; recorded_at: string }>(rows: readonly T[]): T[] {
  const latest = new Map<string, T>();
  for (const row of rows) {
    const current = latest.get(row.evaluation_id);
    if (!current || row.recorded_at > current.recorded_at) latest.set(row.evaluation_id, row);
  }
  return [...latest.values()];
}

export function toSummary(row: EvaluationRecord): EvaluationSummary {
  const { generation_params: _g, evaluation_params: _e, ...summary } = row;
  return summary;
}

const anyOf = <T>(list: readonly T[] | undefined, value: T | null | undefined) =>
  !list?.length || (value !== null && value !== undefined && list.includes(value));

/** The in-memory twin of the `evaluations.list` WHERE clause. */
export function matchesFilter(row: EvaluationSummary, f: EvaluationFilterParsed): boolean {
  if (f.tables?.length && !row.tables.some((t) => t.name && f.tables!.includes(t.name))) return false;
  if (!anyOf(f.engine, row.engine)) return false;
  if (!anyOf(f.llm_model, row.llm_model_uri)) return false;
  if (!anyOf(f.embedder, row.embedder_id)) return false;
  if (!anyOf(f.retrieval, row.retrieval_method)) return false;
  if (!anyOf(f.seed, row.seed)) return false;
  if (f.similarity_min !== undefined && (row.similarity === null || row.similarity < f.similarity_min)) return false;
  if (f.similarity_max !== undefined && (row.similarity === null || row.similarity > f.similarity_max)) return false;
  if (!anyOf(f.reference_rows_limit, row.reference_rows_limit)) return false;
  if (!anyOf(f.num_rows, row.num_rows_requested)) return false;
  if (!anyOf(f.source_stats_tier, row.source_stats_tier)) return false;
  if (!anyOf(f.profiler_version, row.profiler_version)) return false;
  if (!anyOf(f.env, row.env)) return false;
  if (!anyOf(f.status, row.status)) return false;
  if (!anyOf(f.trigger, row.trigger)) return false;
  if (!anyOf(f.runner, row.runner)) return false;
  if (!anyOf(f.mode, row.mode)) return false;
  if (!anyOf(f.evaluator_version, row.evaluator_version)) return false;
  if (!anyOf(f.catalogue_version, row.catalogue_version)) return false;
  if (!anyOf(f.relationship_model, row.relationship_model)) return false;
  if (f.from && Date.parse(row.evaluated_at) < Date.parse(f.from)) return false;
  if (f.to && Date.parse(row.evaluated_at) >= Date.parse(f.to)) return false;
  if (f.q) {
    const q = f.q.toLowerCase();
    const haystack = [
      row.evaluation_id,
      row.base_run_id,
      row.generation_job_id,
      row.generation_job_name,
      ...row.run_ids,
    ];
    if (!haystack.some((s) => s?.toLowerCase().includes(q))) return false;
  }
  return true;
}

/** Sort with NULLs last in both directions (as the SQL `ORDER BY … NULLS LAST`), evaluation_id as tie-break. */
export function sortEvaluations(
  rows: EvaluationSummary[],
  sort: EvaluationFilterParsed["sort"],
  order: "asc" | "desc",
) {
  const sign = order === "asc" ? 1 : -1;
  return rows.sort((a, b) => {
    const x = a[sort];
    const y = b[sort];
    if (x === null && y === null) return a.evaluation_id.localeCompare(b.evaluation_id);
    if (x === null) return 1;
    if (y === null) return -1;
    const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
    return c !== 0 ? sign * c : a.evaluation_id.localeCompare(b.evaluation_id);
  });
}

const sortedDistinct = <T extends string | number>(values: (T | null | undefined)[]): T[] =>
  [...new Set(values.filter((v): v is T => v !== null && v !== undefined))].sort((a, b) =>
    typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b)),
  );

export interface FacetInputs {
  latest: readonly EvaluationSummary[];
  runs: number;
  metrics: number;
  metricIds: readonly string[];
  sourceTables: Facets["source_tables"];
  rag: readonly RagSet[];
  pools: Facets["pools"];
}

export function buildFacets(input: FacetInputs): Facets {
  const rows = input.latest;
  const byDate = [...rows].sort((a, b) => b.evaluated_at.localeCompare(a.evaluated_at));
  const newest = byDate.find((r) => r.event === "FINAL") ?? byDate[0];
  const tables = sortedDistinct(rows.flatMap((r) => r.tables.map((t) => t.name)));
  return {
    counts: {
      evaluations: rows.length,
      runs: input.runs,
      tables: tables.length,
      relationship_models: sortedDistinct(rows.map((r) => r.relationship_model)).length,
      metrics: input.metrics,
    },
    latest: newest
      ? {
          evaluation_id: newest.evaluation_id,
          evaluated_at: newest.evaluated_at,
          status: newest.status,
          overall_score: newest.overall_score,
        }
      : null,
    date_range: byDate.length ? { min: byDate.at(-1)!.evaluated_at, max: byDate[0]!.evaluated_at } : null,
    tables,
    engines: sortedDistinct(rows.map((r) => r.engine)),
    llm_models: sortedDistinct(rows.map((r) => r.llm_model_uri)),
    embedders: sortedDistinct(rows.map((r) => r.embedder_id)),
    retrieval_methods: sortedDistinct(rows.map((r) => r.retrieval_method)),
    seeds: sortedDistinct(rows.map((r) => r.seed)),
    similarities: sortedDistinct(rows.map((r) => r.similarity)),
    reference_rows_limits: sortedDistinct(rows.map((r) => r.reference_rows_limit)),
    num_rows: sortedDistinct(rows.map((r) => r.num_rows_requested)),
    source_stats_tiers: sortedDistinct(rows.map((r) => r.source_stats_tier)),
    profiler_versions: sortedDistinct(rows.map((r) => r.profiler_version)),
    envs: sortedDistinct(rows.map((r) => r.env)),
    statuses: sortedDistinct(rows.map((r) => r.status)),
    triggers: sortedDistinct(rows.map((r) => r.trigger)),
    runners: sortedDistinct(rows.map((r) => r.runner)),
    modes: sortedDistinct(rows.map((r) => r.mode)),
    evaluator_versions: sortedDistinct(rows.map((r) => r.evaluator_version)),
    catalogue_versions: sortedDistinct(rows.map((r) => r.catalogue_version)),
    relationship_models: sortedDistinct(rows.map((r) => r.relationship_model)),
    metric_ids: [...input.metricIds].sort(),
    source_tables: input.sourceTables,
    rag: [...input.rag],
    pools: input.pools,
  };
}

// --------------------------------------------------------------- compare --

export const metricKey = (m: Pick<MetricRow, "metric_id" | "table_name" | "column_name" | "column_name_2" | "edge">) =>
  [m.metric_id, m.table_name, m.column_name ?? "", m.column_name_2 ?? "", m.edge ?? ""].join("|");

const cell = (m: MetricRow): MetricCell => ({
  value: m.value,
  score: m.score,
  status: m.status,
  baseline_value: m.baseline_value,
  noise_floor: m.noise_floor,
  ci_low: m.ci_low,
  ci_high: m.ci_high,
  threshold_warn: m.threshold_warn,
  threshold_fail: m.threshold_fail,
  n_source: m.n_source,
  n_synthetic: m.n_synthetic,
  encoding_plan_digest: m.encoding_plan_digest,
});

type Json = ParamDiff["values"][number];

/** Nested JSON → dotted paths ("generation_params.pool_seed_strategy"); arrays stay leaves. */
export function flatten(value: unknown, prefix: string, out: Map<string, Json>) {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    for (const [k, v] of Object.entries(value as Record<string, unknown>))
      flatten(v, prefix ? `${prefix}.${k}` : k, out);
  } else out.set(prefix, (value ?? null) as Json);
}

const PARAM_COLUMNS = [
  "engine",
  "llm_model_uri",
  "embedder_id",
  "seed",
  "similarity",
  "retrieval_method",
  "reference_rows_limit",
  "num_rows_requested",
  "uniqueness_mode",
  "freetext_expansion",
  "source_stats_tier",
  "profiler_version",
  "write_disposition",
  "env",
  "client_type",
  "vllm_dtype",
  "mode",
  "runner",
  "evaluator_version",
  "catalogue_version",
  "relationship_model",
] as const;

export function buildComparison(
  ids: readonly string[],
  evaluations: readonly EvaluationRecord[],
  metrics: readonly MetricRow[],
): Comparison {
  const byId = new Map(evaluations.map((e) => [e.evaluation_id, e]));
  const present = ids.filter((id) => byId.has(id));
  const ordered = present.map((id) => byId.get(id)!);
  const keys = new Map<string, ComparedMetric>();
  for (const m of metrics) {
    const col = present.indexOf(m.evaluation_id);
    if (col < 0) continue;
    const key = metricKey(m);
    let entry = keys.get(key);
    if (!entry) {
      entry = {
        key,
        metric_id: m.metric_id,
        table_name: m.table_name,
        column_name: m.column_name,
        column_name_2: m.column_name_2,
        edge: m.edge,
        level: m.level,
        family: m.family,
        value_kind: m.value_kind,
        cells: present.map(() => null),
      };
      keys.set(key, entry);
    }
    entry.cells[col] = cell(m);
  }
  const params = new Map<string, Json[]>();
  ordered.forEach((e, i) => {
    const flat = new Map<string, Json>();
    for (const column of PARAM_COLUMNS) flat.set(column, e[column] ?? null);
    flatten(e.generation_params, "generation_params", flat);
    flatten(e.evaluation_params, "evaluation_params", flat);
    for (const [path, value] of flat) {
      const list = params.get(path) ?? ordered.map(() => null as Json);
      list[i] = value;
      params.set(path, list);
    }
  });
  const tableNames = [
    ...new Set(ordered.flatMap((e) => e.tables.map((t) => t.name).filter((n): n is string => !!n))),
  ].sort();
  const encodingPlans = tableNames.map((table) => {
    const digests = ordered.map((e) => e.tables.find((t) => t.name === table)?.encoding_plan_digest ?? null);
    const known = digests.filter((d) => d !== null);
    return { table_name: table, digests, same: new Set(known).size <= 1 };
  });
  const catalogueSame = new Set(ordered.map((e) => e.catalogue_version)).size <= 1;
  const evaluatorSame = new Set(ordered.map((e) => e.evaluator_version)).size <= 1;
  const notComparable: string[] = [];
  if (!catalogueSame) notComparable.push("catalogue_version differs: metric definitions may differ");
  for (const plan of encodingPlans)
    if (!plan.same)
      notComparable.push(`${plan.table_name}: encoding_plan_digest differs (bins, dictionaries or pairs changed)`);
  return {
    evaluations: ordered.map(toSummary),
    missing: ids.filter((id) => !byId.has(id)),
    metrics: [...keys.values()].sort((a, b) => a.key.localeCompare(b.key)),
    params: [...params.entries()]
      .map(([path, values]) => ({ path, values, differs: new Set(values.map((v) => JSON.stringify(v))).size > 1 }))
      .sort((a, b) => a.path.localeCompare(b.path)),
    comparability: {
      catalogue_version_same: catalogueSame,
      evaluator_version_same: evaluatorSame,
      encoding_plans: encodingPlans,
      not_comparable: notComparable,
    },
  };
}

// ----------------------------------------------------------------- trend --

export function toTrendPoint(m: MetricRow, e: EvaluationSummary): TrendPoint {
  return {
    evaluation_id: e.evaluation_id,
    evaluated_at: e.evaluated_at,
    evaluation_status: e.status,
    catalogue_version: e.catalogue_version,
    metric_id: m.metric_id,
    table_name: m.table_name,
    column_name: m.column_name,
    column_name_2: m.column_name_2,
    edge: m.edge,
    status: m.status,
    value: m.value,
    score: m.score,
    baseline_value: m.baseline_value,
    noise_floor: m.noise_floor,
    ci_low: m.ci_low,
    ci_high: m.ci_high,
    threshold_warn: m.threshold_warn,
    threshold_fail: m.threshold_fail,
    n_source: m.n_source,
    n_synthetic: m.n_synthetic,
    engine: e.engine,
    llm_model_uri: e.llm_model_uri,
    embedder_id: e.embedder_id,
    retrieval_method: e.retrieval_method,
    seed: e.seed,
    similarity: e.similarity,
    reference_rows_limit: e.reference_rows_limit,
    num_rows_requested: e.num_rows_requested,
    source_stats_tier: e.source_stats_tier,
  };
}

// ------------------------------------------------------------------- dlq --

const knobList = (id: string) => (knobs.knobs.find((k) => k.id === id)?.value as string[] | undefined) ?? [];
const DECLARED = new Set(knobList("blocker_rules_declared"));
const COUNTED = new Set(knobList("blocker_rule_ids"));

export function blockerFlags(rule: string | null) {
  return { blocker_declared: rule !== null && DECLARED.has(rule), blocker_counted: rule !== null && COUNTED.has(rule) };
}

export function parseJsonText(text: string | null | undefined): unknown {
  if (text === null || text === undefined) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

export function parseCounts(text: string | null): Record<string, number> {
  const value = parseJsonText(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).filter(([, v]) => Number.isInteger(v)),
  ) as Record<string, number>;
}

export function sortDlq(rows: DlqSummary[]): DlqSummary[] {
  return rows.sort(
    (a, b) => a.run_id.localeCompare(b.run_id) || b.count - a.count || (a.rule_id ?? "").localeCompare(b.rule_id ?? ""),
  );
}

// ---------------------------------------------------------- source stats --

/** The table_fqn a query names: exact, or the unique FQN ending in `.<name>`. */
export function resolveTable(fqns: readonly string[], table: string): string | null {
  if (fqns.includes(table)) return table;
  const matches = fqns.filter((f) => f.endsWith(`.${table}`));
  return matches.length === 1 ? matches[0]! : null;
}

export function assembleSourceStats(
  tableFqn: string,
  rows: readonly SourceTableStatsRow[],
  q: { tier?: "sample" | "exact" | undefined; digest?: string | undefined },
): SourceStats {
  const snapshots = new Map<string, SourceStats["snapshots"][number]>();
  for (const row of rows) {
    const key = row.reference_digest;
    const s = snapshots.get(key);
    if (!s)
      snapshots.set(key, {
        reference_digest: key,
        run_id: row.run_id,
        stats_tier: row.stats_tier,
        profiler_version: row.profiler_version,
        sample_rows: row.sample_rows,
        computed_at: row.computed_at,
        columns: 1,
      });
    else {
      s.columns += 1;
      if (row.computed_at > s.computed_at) s.computed_at = row.computed_at;
    }
  }
  const ordered = [...snapshots.values()].sort((a, b) => b.computed_at.localeCompare(a.computed_at));
  let selected: string[];
  if (q.digest) selected = ordered.filter((s) => s.reference_digest === q.digest).map((s) => s.reference_digest);
  else if (q.tier)
    selected = ordered
      .filter((s) => s.stats_tier === q.tier)
      .slice(0, 1)
      .map((s) => s.reference_digest);
  else
    selected = (["exact", "sample"] as const)
      .map((tier) => ordered.find((s) => s.stats_tier === tier)?.reference_digest)
      .filter((d): d is string => !!d);
  const tiers = [...new Set(ordered.map((s) => s.stats_tier))].filter(
    (t): t is "sample" | "exact" => t === "sample" || t === "exact",
  );
  return {
    table_fqn: tableFqn,
    tiers_available: tiers.sort(),
    snapshots: ordered,
    selected,
    columns: rows
      .filter((r) => selected.includes(r.reference_digest))
      .map((r) => {
        const parsed = profilerStatsSchema.safeParse(parseJsonText(r.stats));
        return { ...r, stats_parsed: parsed.success ? parsed.data : null };
      })
      .sort((a, b) => a.reference_digest.localeCompare(b.reference_digest) || a.column.localeCompare(b.column)),
  };
}
