/**
 * Test fixtures for the EVALUATION views: registry rows, metric rows (their
 * level, family and value kind from the catalogue), profiles and comparisons,
 * shaped like the generated BigQuery schemas. Invented names only.
 */
import type { Comparison, EvaluationDetail, EvaluationRecord, MetricRow, ProfileRow, RowFlag } from "@contracts/api";

import { metricMeta } from "../lib/catalogue";

export const T0 = "2026-09-20T10:00:00.123456Z";

type TableEntry = EvaluationRecord["tables"][number];

export function tableEntry(name: string, overrides: Partial<TableEntry> = {}): TableEntry {
  return {
    name,
    landing_table: `demo-project.synthetic_data.${name}`,
    source_table: `demo-project.synthetic_source.${name}`,
    run_id: `run-t-${name}`,
    role: "root",
    scope_mode: "table",
    scope_status: "ok",
    scope_ok: true,
    scope_reason: null,
    window_start: null,
    window_end: null,
    source_snapshot_ts: T0,
    source_drifted: false,
    reference_digest: "d".repeat(64),
    reference_verified: true,
    reference_n: 10_000,
    exposure_n: 1024,
    holdout_n: 10_000,
    rows_source: 100_000,
    rows_synthetic: 1_000_000,
    rows_expected: 1_000_000,
    sampled: false,
    sample_rate_source: null,
    sample_rate_synthetic: null,
    encoding_plan_digest: "plan-a",
    table_score: null,
    ...overrides,
  };
}

export function evaluation(overrides: Partial<EvaluationRecord> = {}): EvaluationRecord {
  return {
    evaluation_id: "eval-t001",
    recorded_at: T0,
    evaluated_at: T0,
    finished_at: "2026-09-20T11:00:00.000000Z",
    event: "FINAL",
    status: "SUCCEEDED",
    status_reason: null,
    evaluation_key: "k".repeat(32),
    trigger: "cli",
    runner: "DataflowRunner",
    mode: "exact",
    evaluator_version: "0.2.0",
    catalogue_version: "1.0.0",
    evaluation_job_id: null,
    image: null,
    generation_job_id: "2026-09-20_09_00_00-1",
    generation_job_name: "synthetic-demo",
    generation_region: "europe-west3",
    generation_started_at: T0,
    generation_finished_at: T0,
    base_run_id: "run-t",
    run_ids: ["run-t-users"],
    params_source: "manual",
    relationship_model: "demo_model",
    relationship_model_sha: "abc123abc123",
    relationship_model_uri: null,
    model_adjusted: false,
    tables: [tableEntry("users")],
    engine: "b1_rag",
    llm_model_uri: "gs://demo/synthetic/models/gemma4/gemma4-e4b/v1/",
    embedder_id: "hashing-384",
    seed: "42",
    similarity: 0.5,
    retrieval_method: "kcenter",
    reference_rows_limit: 10_000,
    num_rows_requested: 1_000_000,
    uniqueness_mode: "exact",
    freetext_expansion: "off",
    source_stats_tier: "exact",
    profiler_version: "2",
    write_disposition: "overwrite",
    env: "dev",
    client_type: "vllm",
    vllm_dtype: "bfloat16",
    generation_params: { engine: "b1_rag", seed: "42", similarity: 0.5, embedder_uri: "" },
    evaluation_params: { mode: "exact", sample_rows: 200_000 },
    overall_score: 0.9,
    fidelity_score: 0.88,
    privacy_score: 0.95,
    integrity_score: 1,
    diversity_score: 0.86,
    metrics_total: 0,
    metrics_pass: 0,
    metrics_warn: 0,
    metrics_fail: 0,
    metrics_not_evaluated: 0,
    bq_bytes_processed: 1_000_000,
    predicted_shuffle_gb: 1,
    warnings: [],
    artifacts_uri: null,
    ...overrides,
  };
}

/** A metric row; level, family and value kind come from the catalogue entry of `metric_id`. */
export function metric(metricId: string, overrides: Partial<MetricRow> = {}): MetricRow {
  const meta = metricMeta(metricId);
  return {
    evaluation_id: "eval-t001",
    evaluated_at: T0,
    table_name: "users",
    landing_table: null,
    source_table: null,
    level: meta?.level ?? "column",
    family: meta?.family ?? "fidelity",
    metric_id: metricId,
    metric_version: "1",
    value_kind: (meta?.value_kind ?? "distance") as MetricRow["value_kind"],
    column_name: null,
    column_name_2: null,
    column_kind: null,
    edge: null,
    value: 0,
    source_value: null,
    synthetic_value: null,
    baseline_value: null,
    score: 1,
    status: "pass",
    threshold_warn: meta?.thresholds.warn ?? null,
    threshold_fail: meta?.thresholds.fail ?? null,
    noise_floor: null,
    noise_floor_method: null,
    ci_low: null,
    ci_high: null,
    n_source: 10_000,
    n_synthetic: 10_000,
    method: "exact",
    sample_rate: null,
    encoding_plan_digest: "plan-a",
    feature_set_digest: null,
    detail: null,
    ...overrides,
  };
}

export function profile(
  overrides: Partial<ProfileRow> & Pick<ProfileRow, "profile_kind" | "side" | "payload">,
): ProfileRow {
  return {
    evaluation_id: "eval-t001",
    evaluated_at: T0,
    table_name: "users",
    column_name: null,
    edge: null,
    n: 10_000,
    truncated: false,
    edges_digest: null,
    ...overrides,
  };
}

export function flag(overrides: Partial<RowFlag> = {}): RowFlag {
  return {
    evaluation_id: "eval-t001",
    evaluated_at: T0,
    table_name: "users",
    check: "near_copy",
    rank: 1,
    synthetic_key: { id: 5_000_001 },
    source_key_hash: "0123456789abcdef0123456789abcdef",
    source_key: null,
    source_set: "R",
    distance: 0.0003,
    score: 0.99,
    detail: null,
    ...overrides,
  };
}

/** Model and table roll-ups for one table. */
export function rollups(table = "users", model = "demo_model", score = 0.9): MetricRow[] {
  const families = ["fidelity", "privacy", "integrity", "diversity", "overall"];
  return [
    ...families.map((f) => metric(`table.${f}_score`, { table_name: table, value: score, score })),
    ...families.map((f) => metric(`model.${f}_score`, { table_name: model, value: score, score })),
  ];
}

/** A small but complete relational evaluation: users ──< orders, a documented edge, privacy lifts, a not-evaluated pair. */
export function richDetail(): EvaluationDetail {
  const metrics: MetricRow[] = [
    // users.age: KS below the noise floor and near its baseline (indistinguishable).
    metric("column.ks", {
      column_name: "age",
      column_kind: "numeric",
      value: 0.004,
      baseline_value: 0.003,
      noise_floor: 0.005,
      noise_floor_method: "ks_two_sample",
      score: 0.96,
      detail: { d_lo: 0.004, d_hi: 0.02 },
    }),
    metric("column.null_rate_delta", {
      column_name: "age",
      column_kind: "numeric",
      value: 0.001,
      source_value: 0.01,
      synthetic_value: 0.011,
      noise_floor: 0.002,
    }),
    // users.created_at: a real FAIL above its floor.
    metric("column.hour_tvd", {
      column_name: "created_at",
      column_kind: "temporal",
      value: 0.25,
      baseline_value: 0.01,
      noise_floor: 0.006,
      noise_floor_method: "tvd_null",
      score: 0,
      status: "fail",
    }),
    // A lift of 3× whose CI lower bound sits under the warn threshold: PASS by the CI gate.
    metric("row.near_match_lift", {
      value: 3,
      ci_low: 0.03,
      ci_high: null,
      noise_floor_method: "rate_ratio",
      detail: { copies_r: 1, copies_h: 0 },
    }),
    metric("row.memorization_lift", {
      value: null,
      score: null,
      status: "not_evaluated",
      noise_floor_method: "rate_ratio",
      detail: { reason: "no copies on either side (m_R = m_H = 0)" },
    }),
    metric("pair.cramers_v_delta", {
      column_name: "gender",
      column_name_2: "state",
      column_kind: "categorical",
      value: null,
      score: null,
      status: "not_evaluated",
      detail: { reason: "Cramér's V is undefined on both sides" },
    }),
    metric("relationship.orphan_rate", {
      table_name: "orders",
      edge: "orders.user_id->users.id",
      value: 0,
      detail: { orphans: 0, enforced: true, role: "driving" },
    }),
    metric("relationship.orphan_rate", {
      table_name: "orders",
      edge: "orders.buyer_id->users.id",
      value: 0.02,
      score: null,
      status: "info",
      detail: { orphans: 40, enforced: false, role: "documented" },
    }),
    metric("relationship.orphan_rate_source", {
      table_name: "orders",
      edge: "orders.buyer_id->users.id",
      value: 0.015,
      score: 0,
      status: "info",
      threshold_warn: null,
      threshold_fail: null,
    }),
    ...rollups("users"),
    ...rollups("orders").filter((m) => m.level === "table"),
  ];
  return {
    evaluation: evaluation({
      tables: [tableEntry("users"), tableEntry("orders", { role: "driven" })],
      metrics_total: metrics.length,
    }),
    events: [],
    metrics,
    profiles: [],
    flags: [flag(), flag({ rank: 2, check: "nearest_record", distance: 0.41 })],
  };
}

/** One table with `columns` numeric columns, three metrics each (the wide-table case). */
export function wideDetail(columns = 200): EvaluationDetail {
  const metrics: MetricRow[] = [];
  for (let i = 0; i < columns; i += 1) {
    const column = `feature_${String(i).padStart(3, "0")}`;
    const failing = i % 37 === 0;
    metrics.push(
      metric("column.ks", {
        table_name: "user_features",
        column_name: column,
        column_kind: "numeric",
        value: failing ? 0.3 : 0.01,
        status: failing ? "fail" : "pass",
        noise_floor: 0.02,
      }),
      metric("column.pit_w1", {
        table_name: "user_features",
        column_name: column,
        column_kind: "numeric",
        value: 0.004,
      }),
      metric("field.range_adherence", {
        table_name: "user_features",
        column_name: column,
        column_kind: "numeric",
        value: 1,
      }),
    );
  }
  metrics.push(...rollups("user_features", "user_features_model"));
  return {
    evaluation: evaluation({
      evaluation_id: "eval-wide",
      relationship_model: "user_features_model",
      tables: [tableEntry("user_features", { role: "isolated" })],
    }),
    events: [],
    metrics,
    profiles: [],
    flags: [],
  };
}

/** Two runs of one metric set, the second measured with another catalogue and encoding plan. */
export function comparison(options: { sameVersions?: boolean } = {}): Comparison {
  const same = options.sameVersions ?? false;
  const a = evaluation({ evaluation_id: "eval-a", evaluated_at: "2026-09-01T10:00:00.000000Z" });
  const b = evaluation({
    evaluation_id: "eval-b",
    evaluated_at: "2026-09-10T10:00:00.000000Z",
    catalogue_version: same ? "1.0.0" : "0.9.0",
    evaluator_version: same ? "0.2.0" : "0.1.0",
  });
  const strip = (e: EvaluationRecord) => {
    const { generation_params: _g, evaluation_params: _e, ...rest } = e;
    return rest;
  };
  const cell = (
    value: number | null,
    overrides: Partial<Comparison["metrics"][number]["cells"][number] & object> = {},
  ) => ({
    value,
    score: value === null ? null : 1,
    status: "pass" as const,
    baseline_value: null,
    noise_floor: null,
    ci_low: null,
    ci_high: null,
    threshold_warn: 0.1,
    threshold_fail: 0.2,
    n_source: 1000,
    n_synthetic: 1000,
    encoding_plan_digest: "plan-a",
    ...overrides,
  });
  const planB = same ? "plan-a" : "plan-b";
  return {
    evaluations: [strip(a), strip(b)],
    missing: [],
    metrics: [
      {
        key: "column.ks|users|age||",
        metric_id: "column.ks",
        table_name: "users",
        column_name: "age",
        column_name_2: null,
        edge: null,
        level: "column",
        family: "fidelity",
        value_kind: "distance",
        cells: [cell(0.03, { noise_floor: 0.02 }), cell(0.045, { noise_floor: 0.02, encoding_plan_digest: planB })],
      },
      {
        key: "column.tvd|users|state||",
        metric_id: "column.tvd",
        table_name: "users",
        column_name: "state",
        column_name_2: null,
        edge: null,
        level: "column",
        family: "fidelity",
        value_kind: "distance",
        cells: [
          cell(0.02, { noise_floor: 0.005 }),
          cell(0.15, { noise_floor: 0.005, status: "warn", encoding_plan_digest: planB }),
        ],
      },
    ],
    params: [
      { path: "engine", values: ["b1_rag", "b1_rag"], differs: false },
      { path: "generation_params.seed", values: ["42", ""], differs: true },
    ],
    comparability: {
      catalogue_version_same: same,
      evaluator_version_same: same,
      encoding_plans: [{ table_name: "users", digests: ["plan-a", planB], same }],
      not_comparable: same
        ? []
        : [
            "catalogue_version differs (1.0.0 vs 0.9.0): metric definitions, thresholds or scoring may differ",
            "users: encoding_plan_digest differs (bins, dictionaries or pairs changed)",
          ],
    },
  };
}

/** richDetail plus a row whose status is newer than the GUI's vocabulary: INFO > 0 and "other" > 0. */
export function countsDetail(): EvaluationDetail {
  const detail = richDetail();
  detail.metrics = [
    ...detail.metrics,
    metric("column.tvd", {
      column_name: "state",
      column_kind: "categorical",
      value: 0.05,
      status: "stale" as MetricRow["status"],
    }),
    metric("column.wasserstein", {
      column_name: "age",
      column_kind: "numeric",
      value: 1.4,
      score: 1.4,
      status: "info",
      threshold_warn: null,
      threshold_fail: null,
    }),
  ];
  return detail;
}
