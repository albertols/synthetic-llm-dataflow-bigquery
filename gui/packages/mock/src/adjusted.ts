/**
 * The adjusted-keys launch (ADR 0038): one relational launch whose declared
 * primary keys the source disproved, and two evaluations of it — the mock's
 * case for the GUI's "Adjusted" reading of `table.pk_duplicate_rate`.
 *
 *     B_TABLE ──< E_TABLE ──< F_TABLE
 *        ├─────< C_TABLE ──< A_TABLE
 *        ├─────< D_TABLE
 *        └─────< G_TABLE
 *
 * Names are the repository's aliases (`B_TABLE`, `COL_001` …). The model,
 * `adjusted_keys_demo`, is invented: it is not under config/relationships and
 * not in relationships.json; the BFF serves it from `/api/relationships` in
 * mock mode, like `thelook_demo`.
 *
 * Unlike the storyline, whose every number is drawn, this launch REPLAYS
 * recorded counts. `LAUNCH` holds, per table, the landed rows, the distinct
 * declared-key tuples, the rows the evaluator counts on a repeated key and
 * the source's key-repeat share. The first five tables carry the counts of one
 * real five-table launch; `D_TABLE` and `G_TABLE` are invented, to show the
 * two verdicts that launch did not produce (over-reproduced, unmeasured).
 * Those counts are typed once, here. Every share, rate, delta, DLQ count,
 * gate ratio and verdict is derived from them with the pipeline's formulas
 * (sdfb_core `landing_repeat_share`, `repeat_share_verdict`, `build_run_summary`;
 * the evaluator's `_key_duplicates`), and every other metric is computed from
 * generated rows by the same `TableEvaluator` the storyline uses.
 *
 * The two evaluations differ in what the registry knows of the launch:
 *   eval-0041  chained to the launch: every table carries its run id, so each joins its
 *              `validation_runs` row exactly. `model_adjusted` is NULL (the launch log was not
 *              read), which is the case the run view fills from `validation_runs`.
 *   eval-0042  the composer DAG an hour later, wired to the wrong job: its `generation_job_id`
 *              is eval-0041's own Dataflow job, an evaluator job. One table kept its run id; the
 *              others carry none, there is no base run id, and the "generation" window is the
 *              evaluator's — so nothing else joins (verdict `unknown`).
 */
import {
  EMPTY_RECORD_COLUMNS,
  EVALUATOR_JOB_NAME_PREFIXES,
  formatEdge,
  knobs,
  type EvaluationDataHistoryRow,
  type EvaluationMetricsRow,
  type EvaluationProfilesRow,
  type EvaluationRowFlagsRow,
  type RelationshipModel,
  type ValidationRunsRow,
} from "@synthetic-platform/contracts";
import {
  headlineCounts,
  modelFamilyScores,
  Random,
  seedFrom,
  sha256Hex,
  type FamilyScores,
} from "@synthetic-platform/stats";

import { edgeMetrics, familyRows, TableEvaluator, tableRollups, type TableContext } from "./evaluate";
import { baseRunId, isoMicros, tableRunIds } from "./ids";
import { MODEL_URIS, type EvalSpec, type Quality } from "./storyline";
import { synthesize } from "./synth";
import { isoFromEpoch, landingFqn, sourceFqn, T0, T1, type ColumnDef, type Row, type TableDef } from "./thelook";

export const ADJUSTED_MODEL = "adjusted_keys_demo";
const RELATIONSHIPS_URI = `gs://synthetic-platform-demo/synthetic/relationships/${ADJUSTED_MODEL}.yaml`;

interface AdjustedTable {
  name: string;
  /** The declared `pk:` of the relationship model. */
  pk: string[];
  /** The driving edge: this table's columns → the parent's (null on the root). */
  parent: { table: string; cols: string[]; refCols: string[] } | null;
  /** Children per parent in the source (the last bucket is "≥"); the launch copies it. */
  fanout: number[];
  /** Rows in the source table. */
  sourceRows: number;
  /** Rows the launch landed. */
  rows: number;
  /** Distinct declared-key tuples among them. */
  distinctKeys: number;
  /** Rows whose declared key occurs at least twice — first rows included (the evaluator's count). */
  duplicateRows: number;
  /** The launch dropped the declared key from the effective model (ADR 0038 D1). */
  adjusted: boolean;
  /** The source's key-repeat share, measured at launch over the declared key; null = not measured. */
  sourceRepeatShare: number | null;
}

/** In generation order (parents first), so run ids are `<base>-NN-<table>` with NN the position here. */
export const LAUNCH: readonly AdjustedTable[] = [
  {
    name: "B_TABLE",
    pk: ["COL_001"],
    parent: null,
    fanout: [],
    sourceRows: 600_000,
    rows: 1_000_000,
    distinctKeys: 1_000_000,
    duplicateRows: 0,
    adjusted: false,
    sourceRepeatShare: null,
  },
  {
    name: "C_TABLE",
    pk: ["COL_001", "COL_003"],
    parent: { table: "B_TABLE", cols: ["COL_001"], refCols: ["COL_001"] },
    fanout: [0.04, 0.14, 0.2, 0.18, 0.44],
    sourceRows: 3_550_000,
    rows: 5_913_652,
    distinctKeys: 3_431_581,
    duplicateRows: 4_001_177,
    adjusted: true,
    sourceRepeatShare: 0.6617,
  },
  {
    // Invented: the fan-out landed doubled on part of the keys, so the key repeats MORE than its source.
    name: "D_TABLE",
    pk: ["COL_001", "COL_005"],
    parent: { table: "B_TABLE", cols: ["COL_001"], refCols: ["COL_001"] },
    fanout: [0.12, 0.3, 0.26, 0.17, 0.15],
    sourceRows: 1_440_000,
    rows: 2_400_000,
    distinctKeys: 1_300_000,
    duplicateRows: 1_880_000,
    adjusted: true,
    sourceRepeatShare: 0.3312,
  },
  {
    name: "E_TABLE",
    pk: ["COL_001"],
    parent: { table: "B_TABLE", cols: ["COL_001"], refCols: ["COL_001"] },
    fanout: [0, 0.01, 0.97, 0.015, 0.005],
    sourceRows: 1_206_000,
    rows: 2_010_415,
    distinctKeys: 1_000_000,
    duplicateRows: 2_010_391,
    adjusted: true,
    sourceRepeatShare: 0.5027,
  },
  {
    // Invented: adjusted off the capacity ladder, with no measurement of the declared key itself,
    // so the launch claims no share (ADR 0038 D4: "a launch that measured none writes none").
    name: "G_TABLE",
    pk: ["COL_001", "COL_006"],
    parent: { table: "B_TABLE", cols: ["COL_001"], refCols: ["COL_001"] },
    fanout: [0.2, 0.48, 0.22, 0.07, 0.03],
    sourceRows: 750_000,
    rows: 1_250_000,
    distinctKeys: 900_000,
    duplicateRows: 650_000,
    adjusted: true,
    sourceRepeatShare: null,
  },
  {
    name: "A_TABLE",
    pk: ["COL_001", "COL_003", "COL_004"],
    parent: { table: "C_TABLE", cols: ["COL_001", "COL_003"], refCols: ["COL_001", "COL_003"] },
    fanout: [0.02, 0.12, 0.16, 0.2, 0.5],
    sourceRows: 9_700_000,
    rows: 16_126_514,
    distinctKeys: 16_126_514,
    duplicateRows: 0,
    adjusted: true,
    sourceRepeatShare: 0.5087,
  },
  {
    name: "F_TABLE",
    pk: ["COL_001", "COL_002"],
    parent: { table: "E_TABLE", cols: ["COL_001"], refCols: ["COL_001"] },
    fanout: [0.03, 0.34, 0.33, 0.18, 0.12],
    sourceRows: 1_890_000,
    rows: 3_144_398,
    distinctKeys: 1_458_608,
    duplicateRows: 2_471_497,
    adjusted: true,
    sourceRepeatShare: 0.6637,
  },
];

const byName = new Map(LAUNCH.map((t) => [t.name, t]));
const edgeLabel = (t: AdjustedTable) =>
  formatEdge(t.name, { cols: t.parent!.cols, ref: t.parent!.table, ref_cols: t.parent!.refCols });

/** `adjusted_keys_demo` in the generated relationships.json shape. */
export const ADJUSTED_RELATIONSHIP_MODEL: RelationshipModel = {
  model: ADJUSTED_MODEL,
  description:
    "invented model under the repository's aliases: B_TABLE is the root, six driven children; five declare a " +
    "pk: their source repeats (ADR 0038)",
  source: "gui/packages/mock/src/adjusted.ts",
  sha12: "",
  generation_order: LAUNCH.map((t) => t.name),
  tables: LAUNCH.map((t) => ({
    name: t.name,
    pk: t.pk,
    identity: [],
    enabled: true,
    note: t.parent ? "" : "root, generated with num_rows rows",
    fk: t.parent
      ? [
          {
            cols: t.parent.cols,
            ref: t.parent.table,
            ref_cols: t.parent.refCols,
            enforced: true,
            drives: true,
            external: false,
            role: "driving" as const,
            drawn_cols: t.parent.cols,
            note: "",
          },
        ]
      : [],
  })),
};
ADJUSTED_RELATIONSHIP_MODEL.sha12 = sha256Hex(JSON.stringify(ADJUSTED_RELATIONSHIP_MODEL.tables)).slice(0, 12);

// ------------------------------------------------------------------ rows --

const key = (name: string, role: "pk" | "fk"): ColumnDef => ({
  name,
  bqType: "INT64",
  kind: "identifier",
  plan: "key",
  role,
});
/** The same three attribute columns on every table: enough for column, pair and row metrics. */
const ATTRIBUTES: ColumnDef[] = [
  { name: "COL_010", bqType: "FLOAT64", kind: "numeric", plan: "numeric" },
  { name: "COL_011", bqType: "STRING", kind: "categorical", plan: "categorical" },
  { name: "COL_012", bqType: "TIMESTAMP", kind: "temporal", plan: "temporal" },
];
const LEVELS = ["K1", "K2", "K3", "K4", "K5"];
const SAMPLE_ROWS = 600;

function tableDef(t: AdjustedTable): TableDef {
  const fk = new Set(t.parent?.cols ?? []);
  const keys = [...new Set([...t.pk, ...fk])];
  return {
    name: t.name,
    sourceRows: t.sourceRows,
    role: t.parent ? "driven" : "root",
    // A key column the driving edge also draws is listed once, as the FK it is generated from.
    columns: [...keys.map((name) => key(name, fk.has(name) ? "fk" : "pk")), ...ATTRIBUTES],
    row(rng, i) {
      const level = rng.weighted([0.42, 0.27, 0.16, 0.1, 0.05]);
      const row: Row = {};
      for (const name of keys) row[name] = fk.has(name) ? 1 + ((i * 7919) % 100_000) : i + 1;
      // The amount depends a little on the level (a numeric ↔ categorical pair).
      row.COL_010 = rng.bernoulli(0.02) ? null : Math.round(Math.exp(rng.normal(4 + level * 0.1, 0.6)) * 100) / 100;
      row.COL_011 = LEVELS[level]!;
      row.COL_012 = isoFromEpoch(T0 + (T1 - T0) * rng.uniform() ** 0.7);
      return row;
    },
  };
}

// ------------------------------------------------------------ the launch --

const QUALITY: Quality = {
  valueLeak: 0,
  rowLeak: 0,
  exposureLeak: 0,
  nearLeak: 0,
  poolCollapse: false,
  corrKeep: 0,
  drift: 0,
  orphanShare: 0,
  invalidShare: 0,
  temporalBlend: false,
};

function specOf(index: number, evaluatedAt: string, trigger: EvalSpec["trigger"], durationMinutes: number): EvalSpec {
  return {
    index,
    id: `eval-${String(index).padStart(4, "0")}`,
    evaluatedAt,
    durationMinutes,
    trigger,
    runner: "DataflowRunner",
    mode: "exact",
    engine: "b1_rag",
    llm: "gemma4-e4b",
    embedder: "bge-small-en-v1.5",
    retrieval: "kcenter",
    seed: "derived",
    similarity: 0.5,
    referenceRowsLimit: 10_000,
    numRows: LAUNCH[0]!.rows,
    sourceStatsTier: "exact",
    env: "dev",
    uniquenessMode: "exact",
    freetextExpansion: "identifiers",
    vllmDtype: "bfloat16",
    // Not `relational`: that flag means the thelook model to the storyline's builders.
    tables: LAUNCH.map((t) => t.name),
    relational: false,
    quality: QUALITY,
  };
}

/** The chained evaluation of the launch, and the composer one wired to the wrong job. */
const CHAINED = specOf(41, "2026-09-13T09:02:11.418253Z", "chained", 38);
const MISWIRED = specOf(42, "2026-09-13T10:02:26.730911Z", "composer", 36);
export const ADJUSTED_EVALUATIONS = [CHAINED.id, MISWIRED.id] as const;

const knobNumber = (id: string, fallback: number) => Number(knobs.knobs.find((k) => k.id === id)?.value ?? fallback);

/** The launch's `validation_runs` rows, as `build_run_summary` folds them (ADR 0038 D3, D4). */
function validationRuns(base: string, runIds: Map<string, string>, finishedAt: string): ValidationRunsRow[] {
  const tolerance = knobNumber("repeat_share_tolerance", 0.05);
  const gate = knobNumber(`blocker_failure_ratio_${CHAINED.env}`, 0.2);
  return LAUNCH.map((t, i): ValidationRunsRow => {
    // An adjusted table runs in streaming mode: pk.duplicate is the rows beyond the first per key,
    // counted on a digest branch, and no row is removed — so every generated row is valid.
    const pkDuplicate = t.rows - t.distinctKeys;
    const byRule: Record<string, number> = t.adjusted ? { "pk.duplicate": pkDuplicate } : {};
    const excluded = t.adjusted ? ["pk.duplicate"] : [];
    const blockerCount = Object.entries(byRule).reduce((acc, [rule, c]) => acc + (excluded.includes(rule) ? 0 : c), 0);
    // gate_total: an excluded rule leaves BOTH sides of the ratio.
    const total =
      t.rows + Object.entries(byRule).reduce((acc, [rule, c]) => acc + (excluded.includes(rule) ? 0 : c), 0);
    const observed = total ? blockerCount / total : 0;
    // landing_repeat_share: pk.duplicate over the rows generated (valid_count + row.duplicate),
    // written only next to a source share; repeat_share_verdict: |delta| <= the tolerance.
    const source = t.sourceRepeatShare;
    const landing = source === null ? null : pkDuplicate / t.rows;
    const delta = source === null || landing === null ? null : landing - source;
    return {
      run_id: runIds.get(t.name)!,
      reference_digest: sha256Hex(`${sourceFqn(t.name)}|${CHAINED.referenceRowsLimit}|${base}`),
      reference_table: sourceFqn(t.name),
      landing_table: landingFqn(t.name),
      engine: CHAINED.engine,
      model_uri: MODEL_URIS[CHAINED.llm],
      env: CHAINED.env,
      num_rows_requested: t.rows,
      valid_count: t.rows,
      dlq_count: Object.values(byRule).reduce((a, b) => a + b, 0),
      blocker_count: blockerCount,
      dlq_by_rule: JSON.stringify(byRule),
      blocker_failure_ratio: gate,
      observed_blocker_ratio: observed,
      status: observed > gate ? "FAILED_BLOCKER" : "PASSED",
      // The generator writes "" on a table it did not adjust, never NULL.
      excluded_blocker_rules: excluded.join(","),
      source_repeat_share: source,
      landing_repeat_share: landing,
      repeat_share_delta: delta,
      repeat_share_within_tolerance: delta === null ? null : Math.abs(delta) <= tolerance,
      // Each table's summary lands as its branch finishes, the last a few minutes before the job ends.
      created_at: isoMicros(Date.parse(finishedAt) - (LAUNCH.length - i) * 90_000, 300 + i),
      ...EMPTY_RECORD_COLUMNS,
    };
  });
}

type TableEntry = EvaluationDataHistoryRow["tables"][number];

function tableEntry(
  spec: EvalSpec,
  t: AdjustedTable,
  runId: string | null,
  digest: string,
  manual: boolean,
): TableEntry {
  return {
    name: t.name,
    landing_table: landingFqn(t.name),
    source_table: sourceFqn(t.name),
    run_id: runId,
    role: t.parent ? "driven" : "root",
    scope_mode: manual ? "manual" : "table",
    scope_status: "ok",
    scope_ok: true,
    scope_reason: null,
    window_start: null,
    window_end: null,
    source_snapshot_ts: isoMicros(Date.parse(spec.evaluatedAt) - 150 * 60_000),
    source_drifted: false,
    reference_digest: digest,
    reference_verified: true,
    reference_n: spec.referenceRowsLimit,
    exposure_n: 1024,
    holdout_n: spec.referenceRowsLimit,
    rows_source: t.sourceRows,
    rows_synthetic: t.rows,
    rows_expected: t.rows,
    sampled: false,
    sample_rate_source: null,
    sample_rate_synthetic: null,
    encoding_plan_digest: sha256Hex(`${t.name}|0.2.0|1.0.0`).slice(0, 16),
    table_score: null,
  };
}

interface Evaluated {
  metrics: EvaluationMetricsRow[];
  profiles: EvaluationProfilesRow[];
  flags: EvaluationRowFlagsRow[];
  scores: Map<string, FamilyScores>;
}

/** Every table's metric rows: columns, pairs and rows from generated samples; keys and edges from LAUNCH. */
function evaluate(spec: EvalSpec, entries: readonly TableEntry[], seed: number): Evaluated {
  const out: Evaluated = { metrics: [], profiles: [], flags: [], scores: new Map() };
  for (const t of LAUNCH) {
    const table = tableDef(t);
    const entry = entries.find((e) => e.name === t.name)!;
    const sourceRng = new Random(seedFrom("adjusted-source", t.name));
    const source = Array.from({ length: SAMPLE_ROWS }, (_, i) => table.row(sourceRng, i));
    const reference = source.slice(0, SAMPLE_ROWS / 2);
    const holdout = source.slice(SAMPLE_ROWS / 2);
    const parentKeys = new Map((t.parent?.cols ?? []).map((c) => [c, SAMPLE_ROWS]));
    const synthetic = synthesize({ table, source, reference, spec, pools: new Map(), rows: SAMPLE_ROWS, parentKeys });
    const ctx: TableContext = {
      spec,
      table,
      evaluatedAt: spec.evaluatedAt,
      landingTable: entry.landing_table,
      sourceTable: entry.source_table,
      source,
      synthetic,
      reference,
      holdout,
      pools: new Map(),
      textDraws: new Map(),
      nSource: t.sourceRows,
      nSynthetic: t.rows,
      rowsSynthetic: t.rows,
      rowsExpected: t.rows,
      nReference: spec.referenceRowsLimit,
      sampleRate: null,
      encodingPlanDigest: entry.encoding_plan_digest!,
      referenceVerified: true,
      profiledNullFraction: new Map(),
      rng: new Random(seedFrom(seed, spec.id, t.name)),
    };
    const ev = new TableEvaluator(ctx);
    for (const column of table.columns) {
      ev.typeValidity(column);
      ev.nullAndEmpty(column);
      if (column.role) continue;
      if (column.kind === "numeric") ev.numeric(column, false);
      else if (column.kind === "temporal") ev.numeric(column, true);
      else ev.categorical(column);
    }
    ev.pairs(
      table.columns.filter((c) => c.kind === "numeric" || c.kind === "temporal"),
      [["COL_011", "COL_010"]],
    );
    ev.rows();
    const distances = ev.result.columnDistances;
    const meanDistance = distances.reduce((a, b) => a + b, 0) / Math.max(distances.length, 1);
    // The evaluator reads the DECLARED model: it counts duplicates over the declared key whether
    // or not the launch still treated it as one.
    ev.tableLevel(Math.min(0.45, 0.02 + meanDistance), {
      columns: t.pk,
      duplicateRows: t.duplicateRows,
      distinctKeys: t.distinctKeys,
    });
    if (t.parent) {
      const parent = byName.get(t.parent.table)!;
      // The launch fans a child out of its parent's DISTINCT landed keys (ADR 0038 D9).
      edgeMetrics(
        ev,
        {
          label: edgeLabel(t),
          enforced: true,
          role: "driving",
          external: false,
          fanout: t.fanout,
          drift: 0,
          orphanShare: 0,
          parentsSource: parent.sourceRows,
          parentsSynthetic: parent.distinctKeys,
        },
        ctx.rng,
      );
    }
    out.scores.set(t.name, tableRollups(ev));
    out.metrics.push(...ev.result.metrics);
    out.profiles.push(...ev.result.profiles);
    out.flags.push(...ev.result.flags);
  }
  // The model roll-up, on the root table's context (no rows of its own).
  const root = tableDef(LAUNCH[0]!);
  const model = new TableEvaluator({
    spec,
    table: root,
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
    encodingPlanDigest: entries[0]!.encoding_plan_digest!,
    referenceVerified: true,
    profiledNullFraction: new Map(),
    rng: new Random(seedFrom(seed, spec.id, "model")),
  });
  familyRows(model, ADJUSTED_MODEL, modelFamilyScores([...out.scores.values()]), "model");
  out.metrics.push(...model.result.metrics);
  return out;
}

export interface AdjustedLaunch {
  registry: EvaluationDataHistoryRow[];
  metrics: EvaluationMetricsRow[];
  profiles: EvaluationProfilesRow[];
  flags: EvaluationRowFlagsRow[];
  validationRuns: ValidationRunsRow[];
}

/**
 * `registryBase` is the storyline's RUNNING-event builder (dataset.ts): the launch parameters, job
 * ids and evaluator versions are filled the same way; what this launch changes is patched on top.
 */
export function buildAdjustedLaunch(
  seed: number,
  registryBase: (spec: EvalSpec, rng: Random) => EvaluationDataHistoryRow,
): AdjustedLaunch {
  const out: AdjustedLaunch = { registry: [], metrics: [], profiles: [], flags: [], validationRuns: [] };
  const base = baseRunId(CHAINED);
  const runIds = tableRunIds(
    base,
    LAUNCH.map((t) => t.name),
  );
  let launchRuns: ValidationRunsRow[] = [];
  let chainedFinal: EvaluationDataHistoryRow | null = null;
  for (const spec of [CHAINED, MISWIRED]) {
    const rng = new Random(seedFrom(seed, spec.id));
    const running = registryBase(spec, rng);
    const miswired = spec === MISWIRED;
    running.relationship_model = ADJUSTED_MODEL;
    running.relationship_model_sha = ADJUSTED_RELATIONSHIP_MODEL.sha12;
    running.relationship_model_uri = RELATIONSHIPS_URI;
    // NULL = unknown: neither evaluation read the launch log's model_adjustment milestones.
    running.model_adjusted = null;
    // ADR 0038 launches keep no generation record: the mock's "no record" evaluations.
    running.generator_version = null;
    running.generator_config_digest = null;
    const params = running.generation_params;
    running.generation_params = {
      ...(params && typeof params === "object" && !Array.isArray(params) ? params : {}),
      relationships_uri: RELATIONSHIPS_URI,
      on_model_conflict: "adjust",
    };
    if (miswired) {
      // The DAG handed the evaluator the chained evaluation's own job as "the generation job".
      running.generation_job_id = chainedFinal!.evaluation_job_id;
      running.generation_job_ids = running.generation_job_id ? [running.generation_job_id] : [];
      running.generation_job_name = `${EVALUATOR_JOB_NAME_PREFIXES[0]}v0-5-3-${chainedFinal!.evaluated_at
        .slice(0, 19)
        .replace(/[-:]/g, "")
        .toLowerCase()}`;
      running.generation_started_at = chainedFinal!.evaluated_at;
      running.generation_finished_at = chainedFinal!.finished_at;
      running.base_run_id = null;
      running.run_ids = [runIds.get(LAUNCH[0]!.name)!];
      running.params_source = "manual";
    } else {
      running.base_run_id = base;
      running.run_ids = [...runIds.values()];
      launchRuns = validationRuns(base, runIds, running.generation_finished_at!);
    }
    const digestOf = (name: string) => launchRuns.find((r) => r.landing_table === landingFqn(name))!.reference_digest!;
    running.tables = LAUNCH.map((t, i) =>
      tableEntry(spec, t, miswired && i > 0 ? null : runIds.get(t.name)!, digestOf(t.name), miswired),
    );
    out.registry.push(structuredClone(running));

    const final = structuredClone(running);
    final.event = "FINAL";
    final.finished_at = isoMicros(Date.parse(spec.evaluatedAt) + spec.durationMinutes * 60_000);
    final.recorded_at = final.finished_at;
    const evaluated = evaluate(spec, final.tables, seed);
    for (const entry of final.tables) entry.table_score = evaluated.scores.get(entry.name ?? "")?.overall ?? null;
    const model = modelFamilyScores([...evaluated.scores.values()]);
    const counts = headlineCounts(evaluated.metrics);
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
    const rows = LAUNCH.reduce((acc, t) => acc + t.sourceRows + t.rows, 0);
    final.bq_bytes_processed = Math.round(rows * 164);
    final.predicted_shuffle_gb = Math.round((rows * 96) / 1e7) / 100;
    final.warnings = miswired
      ? [
          "launch context: no launch_config log entry for the job; tables and scope come from manual input",
          `${LAUNCH.length - 1} of ${LAUNCH.length} tables have no run id: validation_runs has no row of this job's window`,
        ]
      : [];
    final.status = final.warnings.length ? "SUCCEEDED_WITH_WARNINGS" : "SUCCEEDED";
    final.status_reason = final.warnings[0] ?? null;
    out.registry.push(final);
    out.metrics.push(...evaluated.metrics);
    out.profiles.push(...evaluated.profiles);
    out.flags.push(...evaluated.flags);
    if (!miswired) chainedFinal = final;
  }
  out.validationRuns = launchRuns;
  return out;
}
