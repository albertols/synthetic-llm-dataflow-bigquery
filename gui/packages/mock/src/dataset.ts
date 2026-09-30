/**
 * `createMockDataset()` assembles every table the BFF serves in mock mode:
 * the registry events, metric/profile/flag rows, validation runs and their
 * DLQ, fan-out measurements, source-table stats, RAG chunks (Float32
 * embeddings) and free-text pools — one seeded, self-consistent world.
 */
import {
  bqTables,
  canonicalTimestamp,
  knobs,
  type BqField,
  type BqTableName,
  type EvaluationDataHistoryRow,
  type EvaluationMetricsRow,
  type EvaluationProfilesRow,
  type EvaluationRowFlagsRow,
  type FkFanoutStatsRow,
  type FreetextPoolsRow,
  type SourceTableStatsRow,
  type ValidationRunsRow,
  type DlqRow,
} from "@synthetic-platform/contracts";
import {
  headlineCounts,
  modelFamilyScores,
  Random,
  seedFrom,
  sha256Hex,
  tableFamilyScores,
  tvd,
  tvdNullExpectation,
  w1FromBins,
  type FamilyScores,
} from "@synthetic-platform/stats";

import { fanoutCounts, foldedNewcombeCi, sum, TableEvaluator, wilsonCi, type TableContext } from "./evaluate";
import {
  baseRunId,
  dataflowJobId,
  isoMicros,
  launchDigest,
  referenceDigest,
  rowsFor,
  snapshotEra,
  tableRunIds,
} from "./ids";
import { buildRagSets, type RagSet } from "./rag";
import { buildRuns } from "./runs";
import { buildSourceStats } from "./sourceStats";
import { EMBEDDER_URIS, MODEL_URIS, STORYLINE, type EvalSpec } from "./storyline";
import { buildPool, drawTextValues, synthesize, textUniverse, type Pool } from "./synth";
import {
  EDGES,
  edgeLabel,
  MOCK_RELATIONSHIP_MODEL,
  edgeRole,
  landingFqn,
  PROJECT,
  RELATIONSHIP_MODEL,
  sourceFqn,
  TABLES,
  type EdgeDef,
  type Row,
  type TableDef,
} from "./thelook";

export const SAMPLE_ROWS = 1500;
export const WIDE_SAMPLE_ROWS = 400;
const EVALUATOR_VERSIONS = ["0.1.0", "0.2.0"];
/** The catalogue moved 0.9.0 → 1.0.0 after the first ten evaluations (not_comparable across it). */
export const catalogueVersionOf = (spec: Pick<EvalSpec, "index">) => (spec.index <= 10 ? "0.9.0" : "1.0.0");
const RELATIONSHIPS_URI = `gs://synthetic-platform-demo/synthetic/relationships/${RELATIONSHIP_MODEL}.yaml`;

/** Every TIMESTAMP (recursively through RECORD / REPEATED) in the canonical wire form. */
function canonicalize<T extends object>(rows: T[], table: BqTableName): T[] {
  const walk = (value: unknown, fields: readonly BqField[]) => {
    const row = value as Record<string, unknown>;
    for (const field of fields) {
      const v = row[field.name];
      if (v === null || v === undefined) continue;
      const items: unknown[] = field.mode === "REPEATED" && Array.isArray(v) ? (v as unknown[]) : [v];
      if (field.type === "TIMESTAMP") {
        const fixed = items.map((x): unknown => (typeof x === "string" ? (canonicalTimestamp(x) ?? x) : x));
        row[field.name] = field.mode === "REPEATED" ? fixed : fixed[0];
      } else if (field.type === "RECORD" || field.type === "STRUCT")
        for (const item of items) if (item && typeof item === "object") walk(item, field.fields ?? []);
    }
  };
  const fields: readonly BqField[] = bqTables[table].fields;
  for (const row of rows) walk(row, fields);
  return rows;
}

export interface MockDataset {
  generatedWith: { seed: number; evaluations: number };
  registry: EvaluationDataHistoryRow[];
  metrics: EvaluationMetricsRow[];
  profiles: EvaluationProfilesRow[];
  flags: EvaluationRowFlagsRow[];
  validationRuns: ValidationRunsRow[];
  dlq: DlqRow[];
  fanoutStats: FkFanoutStatsRow[];
  sourceStats: SourceTableStatsRow[];
  rag: RagSet[];
  pools: FreetextPoolsRow[];
  /** Source sample rows per table (for tests and the RAG row documents). */
  sourceSamples: Record<string, Row[]>;
}

function familyRows(ev: TableEvaluator, table: string, scores: FamilyScores, level: "table" | "model") {
  const prefix = level === "table" ? "table" : "model";
  for (const family of ["fidelity", "privacy", "integrity", "diversity", "overall"] as const) {
    const id = `${prefix}.${family}_score` as const;
    const row = ev.metric(id, { value: scores[family], nSource: null, nSynthetic: null, method: "exact" });
    row.table_name = table;
  }
}

function relationship(ev: TableEvaluator, edge: EdgeDef, spec: EvalSpec, rng: Random) {
  const label = edgeLabel(edge);
  const parent = TABLES[edge.parent]!;
  const parentsSource = parent.sourceRows;
  const parentsSynthetic = edge.external ? parent.sourceRows : rowsFor(spec, parent);
  const hs = fanoutCounts(rng, edge.fanout, parentsSource);
  const shares = edge.fanout.map((s, i) => s * (1 + spec.quality.drift * (i - 2) * 0.5));
  const hy = fanoutCounts(rng, shares, parentsSynthetic);
  const fanout = edge.fanout.map((_, i) => i);
  const mean = (h: number[]) => h.reduce((acc, c, i) => acc + c * i, 0) / Math.max(sum(h), 1);
  for (const [side, h, n] of [
    ["source", hs, parentsSource],
    ["synthetic", hy, parentsSynthetic],
  ] as const)
    ev.profile(
      "fanout_hist",
      side,
      {
        fanout,
        counts: h,
        capped_at: fanout.length - 1,
        parents: n,
        children: h.reduce((acc, c, i) => acc + c * i, 0),
        mean: mean(h),
        zero_child_share: h[0]! / n,
      },
      { edge: label, n },
    );
  const children = hy.reduce((acc, c, i) => acc + c * i, 0);
  const sourceChildren = sum(hs.map((c, i) => c * i));
  // Enforced edges orphan only when the external key pool lags (quality.orphanShare); the
  // documented edge copies user_id from the order, so it has none — while the source,
  // with deleted users, has a few: the INFO comparison the catalogue describes.
  const orphans = edge.external ? rng.binomial(children, spec.quality.orphanShare) : 0;
  const sourceOrphans = edge.enforced ? 0 : rng.binomial(sourceChildren, 0.0021);
  // A documented edge (enforced: false) is context, not a verdict: the scorer writes its orphan
  // rate as INFO, unscored and out of the roll-ups (Ruling R42).
  ev.metric("relationship.orphan_rate", {
    edge: label,
    value: orphans / Math.max(children, 1),
    sourceValue: sourceOrphans / Math.max(sourceChildren, 1),
    nSource: sourceChildren,
    nSynthetic: children,
    detail: { orphans, enforced: edge.enforced, role: edgeRole(edge) },
    enforced: edge.enforced,
  });
  ev.metric("relationship.orphan_rate_source", {
    edge: label,
    value: sourceOrphans / Math.max(sourceChildren, 1),
    nSource: sourceChildren,
    detail: { orphans: sourceOrphans, enforced: edge.enforced, role: edgeRole(edge) },
  });
  ev.metric("relationship.fanout_tvd", {
    edge: label,
    enforced: edge.enforced,
    value: tvd(hs, hy),
    noiseFloor: tvdNullExpectation(hs, parentsSource, parentsSynthetic),
    nSource: parentsSource,
    nSynthetic: parentsSynthetic,
  });
  ev.metric("relationship.fanout_w1", {
    edge: label,
    value: w1FromBins([0.5, 1.5, 2.5, 3.5], hs, hy),
    nSource: parentsSource,
    nSynthetic: parentsSynthetic,
  });
  ev.metric("relationship.fanout_mean_ratio", {
    edge: label,
    value: mean(hy) / mean(hs),
    sourceValue: mean(hs),
    syntheticValue: mean(hy),
    nSource: parentsSource,
    nSynthetic: parentsSynthetic,
  });
  ev.metric("relationship.zero_child_share_delta", {
    edge: label,
    value: Math.abs(hy[0]! / parentsSynthetic - hs[0]! / parentsSource),
    ...foldedNewcombeCi(hy[0]!, parentsSynthetic, hs[0]!, parentsSource),
    nSource: parentsSource,
    nSynthetic: parentsSynthetic,
  });
  ev.metric("relationship.cardinality_adherence", {
    edge: label,
    value: 1 - orphans / Math.max(children, 1),
    ...(children > 0 ? wilsonCi(children - orphans, children) : {}),
    nSynthetic: children,
  });
  const covered = (h: number[]) => 1 - h[0]! / Math.max(sum(h), 1);
  ev.metric("relationship.parent_coverage", {
    edge: label,
    value: covered(hy) / covered(hs),
    sourceValue: covered(hs),
    syntheticValue: covered(hy),
    nSource: parentsSource,
    nSynthetic: parentsSynthetic,
  });
}

interface Samples {
  source: Row[];
  reference: Row[];
  holdout: Row[];
}

function sourceSamples(): Record<string, Samples> {
  const out: Record<string, Samples> = {};
  for (const table of Object.values(TABLES)) {
    const rng = new Random(seedFrom("source", table.name));
    const n = table.role === "isolated" ? WIDE_SAMPLE_ROWS : SAMPLE_ROWS;
    const source = Array.from({ length: n }, (_, i) => table.row(rng, i));
    out[table.name] = { source, reference: source.slice(0, n / 2), holdout: source.slice(n / 2) };
  }
  return out;
}

const minutes = (iso: string, delta: number) => isoMicros(Date.parse(iso) + delta * 60_000);

/** Rows per side in sampled mode: the spec's --sample_rows, else the eval_sample_rows knob. */
function sampleRowsOf(spec: EvalSpec): number {
  return spec.sampleRows ?? Number(knobs.knobs.find((k) => k.id === "eval_sample_rows")?.value ?? 200_000);
}

function registryBase(spec: EvalSpec, rng: Random): EvaluationDataHistoryRow {
  const jobRng = new Random(seedFrom("generation-job", spec.id));
  const evaluatorVersion = spec.index <= 20 ? EVALUATOR_VERSIONS[0]! : EVALUATOR_VERSIONS[1]!;
  const catalogueVersion = catalogueVersionOf(spec);
  const eval_ = Object.fromEntries(
    knobs.knobs.filter((k) => k.channel === "evaluation").map((k) => [k.id.replace(/^eval_/, ""), k.value]),
  ) as Record<string, unknown>;
  const evaluationParams = {
    ...eval_,
    ...(spec.sampleRows === undefined ? {} : { sample_rows: spec.sampleRows }),
    mode: spec.mode,
    scope: spec.scopeStatus === "empty" ? "appends" : "auto",
    runner: spec.runner,
  } as EvaluationDataHistoryRow["evaluation_params"];
  const base = baseRunId(spec);
  const tables = spec.relational ? ["users", "orders", "order_items"] : spec.tables;
  return {
    evaluation_id: spec.id,
    recorded_at: spec.evaluatedAt,
    evaluated_at: spec.evaluatedAt,
    finished_at: null,
    event: "RUNNING",
    status: "RUNNING",
    status_reason: null,
    evaluation_key: sha256Hex(`${spec.id}|${evaluatorVersion}|${catalogueVersion}`).slice(0, 32),
    trigger: spec.trigger,
    runner: spec.runner,
    mode: spec.mode,
    evaluator_version: evaluatorVersion,
    catalogue_version: catalogueVersion,
    evaluation_job_id:
      spec.runner === "DataflowRunner"
        ? dataflowJobId(Date.parse(spec.evaluatedAt), (lo, hi) => rng.int(lo, hi))
        : null,
    image: `europe-west3-docker.pkg.dev/${PROJECT}/sdfb/sdfb-evaluation:${evaluatorVersion}`,
    generation_job_id: dataflowJobId(Date.parse(spec.evaluatedAt) - 150 * 60_000, (lo, hi) => jobRng.int(lo, hi)),
    generation_job_name: `synthetic-sdfb-v0-5-3-${spec.llm.replace(/[^a-z0-9]+/g, "-")}`.slice(0, 63),
    generation_region: "europe-west3",
    generation_started_at: minutes(spec.evaluatedAt, -150),
    generation_finished_at: minutes(spec.evaluatedAt, -30),
    base_run_id: base,
    run_ids: [...tableRunIds(base, tables).values()],
    params_source: "jobs_labels+logs",
    relationship_model: spec.relational ? RELATIONSHIP_MODEL : null,
    relationship_model_sha: spec.relational ? MOCK_RELATIONSHIP_MODEL.sha12 : null,
    relationship_model_uri: spec.relational ? RELATIONSHIPS_URI : null,
    model_adjusted: spec.relational ? spec.index === 29 : null,
    tables: [],
    engine: spec.engine,
    llm_model_uri: MODEL_URIS[spec.llm],
    embedder_id: spec.embedder,
    seed: spec.seed,
    similarity: spec.similarity,
    retrieval_method: spec.retrieval,
    reference_rows_limit: spec.referenceRowsLimit,
    num_rows_requested: spec.numRows,
    uniqueness_mode: spec.uniquenessMode,
    freetext_expansion: spec.freetextExpansion,
    source_stats_tier: spec.sourceStatsTier,
    profiler_version: "2",
    write_disposition: spec.index % 3 === 0 ? "overwrite" : "append",
    env: spec.env,
    client_type: "vllm",
    vllm_dtype: spec.vllmDtype,
    generation_params: {
      engine: spec.engine,
      model_uri: MODEL_URIS[spec.llm],
      embedder_uri: EMBEDDER_URIS[spec.embedder],
      similarity: spec.similarity,
      seed: spec.seed === "derived" ? "" : spec.seed,
      pool_seed_strategy: spec.retrieval,
      reference_rows_limit: spec.referenceRowsLimit,
      num_rows: spec.numRows,
      uniqueness_mode: spec.uniquenessMode,
      driven_uniqueness_mode: "streaming",
      freetext_expansion: spec.freetextExpansion,
      source_stats: spec.sourceStatsTier,
      prompt_constraints: "on",
      env: spec.env,
      vllm_dtype: spec.vllmDtype,
      vllm_max_model_len: "8192",
      multi_table_mode: "single_job",
      landing_table: tables.map((t) => landingFqn(t)).join(","),
      reference_table: sourceFqn(tables[tables.length - 1]!),
      relationships_uri: spec.relational ? RELATIONSHIPS_URI : "",
      build_rag_layer: "true",
      build_pool_layer: "true",
    },
    evaluation_params: evaluationParams,
    overall_score: null,
    fidelity_score: null,
    privacy_score: null,
    integrity_score: null,
    diversity_score: null,
    metrics_total: null,
    metrics_pass: null,
    metrics_warn: null,
    metrics_fail: null,
    metrics_not_evaluated: null,
    metrics_info: null,
    bq_bytes_processed: null,
    predicted_shuffle_gb: null,
    warnings: [],
    artifacts_uri: `gs://synthetic-platform-demo/synthetic/evaluations/${spec.id}/`,
  };
}

type TableEntry = EvaluationDataHistoryRow["tables"][number];

function tableEntry(spec: EvalSpec, table: TableDef, runIds: Map<string, string>): TableEntry {
  const rowsExpected = rowsFor(spec, table);
  const mismatch = spec.scopeStatus === "count_mismatch" && table.name === "orders";
  const empty = spec.scopeStatus === "empty";
  const note = spec.scopeNote?.table === table.name ? spec.scopeNote : null;
  const rowsSynthetic = empty ? 0 : mismatch ? Math.round(rowsExpected * 0.969635) : rowsExpected;
  const sampled = spec.mode === "sampled";
  const sampleRows = sampleRowsOf(spec);
  const verified = spec.referenceVerified ?? true;
  return {
    name: table.name,
    landing_table: table.role === "external" ? landingFqn(table.name) : landingFqn(table.name),
    source_table: sourceFqn(table.name),
    run_id: runIds.get(table.name) ?? null,
    role: table.role,
    scope_mode: empty ? "appends" : "table",
    scope_status: empty ? "empty" : mismatch ? "count_mismatch" : (note?.status ?? "ok"),
    scope_ok: !empty && !mismatch && !note,
    scope_reason: empty || mismatch ? (spec.statusReason ?? null) : (note?.reason ?? null),
    window_start: empty ? minutes(spec.evaluatedAt, -24 * 60) : null,
    window_end: empty ? spec.evaluatedAt : null,
    source_snapshot_ts: minutes(spec.evaluatedAt, -150),
    source_drifted: !verified,
    reference_digest: table.role === "external" ? null : launchDigest(spec, table.name),
    reference_verified: table.role === "external" ? null : verified,
    reference_n: table.role === "external" ? null : spec.referenceRowsLimit,
    exposure_n: table.role === "external" ? null : 1024,
    holdout_n: table.role === "external" ? null : spec.referenceRowsLimit,
    rows_source: table.sourceRows,
    rows_synthetic: table.role === "external" ? table.sourceRows : rowsSynthetic,
    rows_expected: table.role === "external" ? table.sourceRows : rowsExpected,
    sampled,
    sample_rate_source: sampled ? Math.min(1, sampleRows / table.sourceRows) : null,
    sample_rate_synthetic: sampled ? Math.min(1, sampleRows / Math.max(rowsSynthetic, 1)) : null,
    encoding_plan_digest:
      table.role === "external"
        ? null
        : sha256Hex(`${table.name}|${spec.index <= 20 ? "0.1.0" : "0.2.0"}|${catalogueVersionOf(spec)}`).slice(0, 16),
    table_score: null,
  };
}

/**
 * Metrics whose catalogue score function is `none` carry score = value (a
 * roll-up's own score). On a measured metric that reads backwards (a drift of
 * 0.01 would score 0.01), so the mock keeps them out of the family means.
 */

const NUMERIC_PAIRS: Record<string, string[]> = {
  users: ["age", "created_at"],
  orders: ["created_at", "shipped_at", "delivered_at", "num_of_item"],
  order_items: ["created_at", "sale_price"],
  user_features: ["feat_001", "feat_002", "feat_003", "feat_004", "feat_005", "feat_006"],
};
const CATEGORICAL_PAIRS: Record<string, [string, string][]> = {
  users: [
    ["gender", "traffic_source"],
    ["country", "traffic_source"],
  ],
  orders: [["status", "num_of_item"]],
  order_items: [["status", "sale_price"]],
  user_features: [["feat_007", "feat_008"]],
};

export function createMockDataset(seed = 20260928): MockDataset {
  const samples = sourceSamples();
  const sourceStats = buildSourceStats(samples);
  const profiled = new Map<string, Map<string, number>>();
  for (const row of sourceStats) {
    const key = `${row.table_fqn}|${row.reference_digest}|${row.stats_tier ?? "sample"}`;
    const map = profiled.get(key) ?? new Map<string, number>();
    if (row.null_fraction !== null) map.set(row.column, row.null_fraction);
    profiled.set(key, map);
  }

  const registry: EvaluationDataHistoryRow[] = [];
  const metrics: EvaluationMetricsRow[] = [];
  const profiles: EvaluationProfilesRow[] = [];
  const flags: EvaluationRowFlagsRow[] = [];
  const poolsByKey = new Map<string, FreetextPoolsRow>();

  for (const spec of STORYLINE) {
    const rng = new Random(seedFrom(seed, spec.id));
    const running = registryBase(spec, rng);
    const base = running.base_run_id!;
    const tableDefs = (spec.relational ? ["users", "orders", "order_items", "products"] : spec.tables).map(
      (t) => TABLES[t]!,
    );
    const runIds = tableRunIds(
      base,
      tableDefs.filter((t) => t.role !== "external").map((t) => t.name),
    );
    running.tables = tableDefs.map((t) => tableEntry(spec, t, runIds));
    registry.push(structuredClone(running));
    if (spec.outcome === "RUNNING") continue;

    const final: EvaluationDataHistoryRow = structuredClone(running);
    final.event = "FINAL";
    final.finished_at = minutes(spec.evaluatedAt, spec.durationMinutes);
    final.recorded_at = final.finished_at;

    if (spec.outcome === "FAILED" || spec.outcome === "SKIPPED") {
      final.status = spec.outcome;
      final.status_reason = spec.statusReason ?? null;
      final.metrics_total = 0;
      final.metrics_pass = 0;
      final.metrics_warn = 0;
      final.metrics_fail = 0;
      final.metrics_not_evaluated = 0;
      final.metrics_info = 0;
      final.bq_bytes_processed = spec.outcome === "FAILED" ? 0 : 1_048_576;
      final.warnings = spec.outcome === "SKIPPED" ? ["scope: appends window is empty"] : [];
      registry.push(final);
      continue;
    }

    const evaluatedRows: EvaluationMetricsRow[] = [];
    const tableScores = new Map<string, FamilyScores>();
    for (const entry of final.tables) {
      const table = TABLES[entry.name ?? ""]!;
      if (table.role === "external") continue;
      const { source, reference, holdout } = samples[table.name]!;
      const digest = entry.reference_digest!;
      const pools = new Map<string, Pool>();
      for (const column of table.columns) {
        if (column.plan !== "freetext_llm_pool" && !(column.plan === "shaped_identifier" && spec.quality.poolCollapse))
          continue;
        const pool = buildPool(table, column, reference, spec, digest);
        pools.set(column.name, pool);
        const key = `${digest}|${MODEL_URIS[spec.llm]}|${column.name}`;
        if (!poolsByKey.has(key))
          poolsByKey.set(key, {
            reference_digest: digest,
            model_uri: MODEL_URIS[spec.llm],
            column: column.name,
            target: pool.target,
            values: pool.values,
            stagnated: pool.stagnated,
            attempts: pool.attempts,
          });
      }
      const parentKeys = new Map<string, number>();
      for (const column of table.columns.filter((c) => c.role === "fk")) {
        const parentTable = column.name === "product_id" ? "products" : column.name === "order_id" ? "orders" : "users";
        parentKeys.set(
          column.name,
          Math.min(
            TABLES[parentTable]!.role === "external" ? TABLES[parentTable]!.sourceRows : SAMPLE_ROWS,
            SAMPLE_ROWS,
          ),
        );
      }
      const inputs = { table, source, reference, spec, pools, rows: source.length, parentKeys };
      const rows = synthesize(inputs);
      const textDraws = new Map<string, string[]>();
      for (const column of table.columns)
        if (textUniverse(table.name, column.name))
          textDraws.set(column.name, drawTextValues(inputs, column.name, 12_000));
      const sampled = spec.mode === "sampled";
      const sampleRows = sampleRowsOf(spec);
      const ctx: TableContext = {
        spec,
        table,
        evaluatedAt: spec.evaluatedAt,
        landingTable: entry.landing_table,
        sourceTable: entry.source_table,
        source,
        synthetic: rows,
        reference,
        holdout,
        pools,
        nSource: sampled ? Math.min(sampleRows, table.sourceRows) : table.sourceRows,
        nSynthetic: sampled ? Math.min(sampleRows, entry.rows_synthetic!) : entry.rows_synthetic!,
        rowsSynthetic: entry.rows_synthetic!,
        rowsExpected: entry.rows_expected!,
        nReference: spec.referenceRowsLimit,
        sampleRate: sampled ? entry.sample_rate_synthetic : null,
        encodingPlanDigest: entry.encoding_plan_digest!,
        referenceVerified: entry.reference_verified ?? true,
        textDraws,
        profiledNullFraction: profiled.get(`${entry.source_table}|${digest}|${spec.sourceStatsTier}`) ?? new Map(),
        rng: new Random(seedFrom(seed, spec.id, table.name)),
      };
      const ev = new TableEvaluator(ctx);
      const wide = table.role === "isolated";
      for (const column of table.columns) {
        if (column.role) {
          ev.typeValidity(column);
          ev.nullAndEmpty(column);
          continue;
        }
        ev.typeValidity(column);
        ev.nullAndEmpty(column);
        if (column.kind === "numeric") ev.numeric(column, false);
        else if (column.kind === "temporal") ev.numeric(column, true);
        else if (column.kind === "categorical" || column.kind === "boolean") ev.categorical(column);
        else ev.text(column);
        if (!wide) ev.sourceStatsDrift(column);
      }
      const numericPairs = (NUMERIC_PAIRS[table.name] ?? []).map((n) => table.columns.find((c) => c.name === n)!);
      ev.pairs(numericPairs, CATEGORICAL_PAIRS[table.name] ?? []);
      ev.rows();
      const distances = ev.result.columnDistances;
      const meanDistance = distances.reduce((a, b) => a + b, 0) / Math.max(distances.length, 1);
      ev.tableLevel(Math.min(0.45, 0.02 + meanDistance + 2 * spec.quality.rowLeak));
      if (spec.relational)
        for (const edge of EDGES.filter((e) => e.child === table.name)) relationship(ev, edge, spec, ctx.rng);

      // Roll-ups (Ruling R11) and the SDMetrics-style shape/trend scores.
      // The evaluator's roll-up (aggregate_scores): score: none measured rows score null (R26).
      const scores = tableFamilyScores(ev.result.metrics);
      const columnFidelity = ev.result.metrics.filter(
        (m) => m.family === "fidelity" && m.level === "column" && m.score !== null,
      );
      const byColumn = new Map<string, number[]>();
      for (const m of columnFidelity)
        byColumn.set(m.column_name ?? "", [...(byColumn.get(m.column_name ?? "") ?? []), m.score!]);
      const meanOf = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
      ev.metric("table.column_shape_score", {
        value: meanOf([...byColumn.values()].map((xs) => meanOf(xs)!)),
        nSource: null,
        nSynthetic: null,
      });
      ev.metric("table.pair_trend_score", {
        value: meanOf(ev.result.metrics.filter((m) => m.level === "pair" && m.score !== null).map((m) => m.score!)),
        nSource: null,
        nSynthetic: null,
      });
      familyRows(ev, table.name, scores, "table");
      tableScores.set(table.name, scores);
      entry.table_score = scores.overall;
      evaluatedRows.push(...ev.result.metrics);
      profiles.push(...ev.result.profiles);
      flags.push(...ev.result.flags);
    }
    // Model roll-up, on the root table's evaluator context.
    const model = modelFamilyScores([...tableScores.values()]);
    const rootTable = final.tables.find((t) => t.role !== "external")!;
    const modelEvaluator = new TableEvaluator({
      spec,
      table: TABLES[rootTable.name ?? ""]!,
      evaluatedAt: spec.evaluatedAt,
      landingTable: null,
      sourceTable: null,
      source: [],
      synthetic: [],
      reference: [],
      holdout: [],
      pools: new Map(),
      textDraws: new Map(),
      nSource: 0,
      nSynthetic: 0,
      rowsSynthetic: 0,
      rowsExpected: 1,
      nReference: spec.referenceRowsLimit,
      sampleRate: null,
      encodingPlanDigest: rootTable.encoding_plan_digest!,
      referenceVerified: true,
      profiledNullFraction: new Map(),
      rng,
    });
    familyRows(modelEvaluator, spec.relational ? RELATIONSHIP_MODEL : (rootTable.name ?? ""), model, "model");
    evaluatedRows.push(...modelEvaluator.result.metrics);
    metrics.push(...evaluatedRows);

    // headline_counts: aggregate rows restate the measured ones and are not counted (R37, R43).
    const counts = headlineCounts(evaluatedRows);
    final.overall_score = model.overall;
    final.fidelity_score = model.fidelity;
    final.privacy_score = model.privacy;
    final.integrity_score = model.integrity;
    final.diversity_score = model.diversity;
    final.metrics_total = counts.total;
    final.metrics_pass = counts.pass;
    final.metrics_warn = counts.warn;
    final.metrics_fail = counts.fail;
    final.metrics_not_evaluated = counts.not_evaluated;
    final.metrics_info = counts.info;
    const rows = final.tables.reduce((acc, t) => acc + (t.rows_source ?? 0) + (t.rows_synthetic ?? 0), 0);
    final.bq_bytes_processed = Math.round(rows * (spec.mode === "sampled" ? 36 : 164));
    final.predicted_shuffle_gb = Math.round((rows * 96) / 1e7) / 100;
    // Operational warnings (not metric results: those are metrics_fail / metrics_warn).
    const warnings: string[] = [];
    if (spec.mode === "sampled")
      warnings.push(`sampled mode: ${sampleRowsOf(spec)} rows per side; n-dependent metrics at matched n`);
    if (spec.referenceVerified === false)
      warnings.push("reference digest not verified: privacy lifts and DCR not evaluated");
    if (spec.scopeStatus === "count_mismatch") warnings.push("orders: rows in scope differ from rows_expected");
    if (spec.scopeNote)
      warnings.push(`${spec.scopeNote.table}: scope ${spec.scopeNote.status}: ${spec.scopeNote.reason}`);
    if (final.model_adjusted)
      warnings.push("relationship model adjusted at launch (ADR 0038): a declared pk the source disproved was dropped");
    if (spec.index === 9) warnings.push("2 BigQuery queries retried after slot contention");
    if (!spec.relational) warnings.push("user_features: 200 columns; null patterns are not profiled above 64 columns");
    final.warnings = warnings;
    const partial = spec.scopeStatus === "count_mismatch" || spec.referenceVerified === false;
    final.status = partial ? "PARTIAL" : warnings.length ? "SUCCEEDED_WITH_WARNINGS" : "SUCCEEDED";
    final.status_reason = partial ? (spec.statusReason ?? null) : warnings.length ? warnings[0]! : null;
    registry.push(final);
  }

  const { validationRuns, dlq, fanoutStats } = buildRuns(STORYLINE, seed);
  let rag: RagSet[] | null = null;
  return {
    generatedWith: { seed, evaluations: STORYLINE.length },
    registry: canonicalize(registry, "evaluation_data_history"),
    metrics: canonicalize(metrics, "evaluation_metrics"),
    profiles: canonicalize(profiles, "evaluation_profiles"),
    flags: canonicalize(flags, "evaluation_row_flags"),
    validationRuns: canonicalize(validationRuns, "validation_runs"),
    dlq: canonicalize(dlq, "dlq"),
    fanoutStats: canonicalize(fanoutStats, "fk_fanout_stats"),
    sourceStats: canonicalize(sourceStats, "source_table_stats"),
    /** Built on first access (the embeddings are the slowest part). */
    get rag() {
      rag ??= buildRagSets(samples);
      return rag;
    },
    pools: [...poolsByKey.values()],
    sourceSamples: Object.fromEntries(Object.entries(samples).map(([k, v]) => [k, v.source])),
  };
}

let cached: MockDataset | null = null;

/** The process-wide dataset (built once, on first use). */
export function getMockDataset(): MockDataset {
  cached ??= createMockDataset();
  return cached;
}

export { referenceDigest, baseRunId, rowsFor, snapshotEra, launchDigest, tableRunIds };
