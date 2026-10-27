/**
 * The isolated-forest launch (ADR 0045): one whole-model launch of an invented model of
 * 120 unrelated tables — no foreign key anywhere — and its one evaluation, `eval-0043`: the mock's
 * case for the run view's isolated-tables views (tile grid, N/A key checks, model summary card,
 * search, filters, windowed per-table lists).
 *
 *     T_001 … T_120   no edges · every 15th table enabled: false (8) · every third declares no PK
 *     112 generated   12 batch jobs of at most 10 tables (b00 … b11), in name order
 *
 * Names are generic aliases; the model is not under config/relationships and the BFF serves it from
 * `/api/relationships` in mock mode. Rules on the table's number decide every fact of the model
 * (key, disabled, source and row-count overrides, the two tables whose landing holds a few duplicate
 * keys under streaming uniqueness), so tests can name tables; those duplicate counts are typed here
 * once. Every metric row is computed from generated samples by the storyline's `TableEvaluator`, with
 * the helpers the adjusted-keys launch uses (adjusted.ts, evaluate.ts).
 */
import {
  EMPTY_RECORD_COLUMNS,
  type EvaluationDataHistoryRow,
  type GeneratorSnapshot,
  type RelationshipModel,
  type ValidationRunsRow,
} from "@synthetic-platform/contracts";
import { Random, seedFrom, sha256Hex } from "@synthetic-platform/stats";

import {
  ATTRIBUTE_PAIRS,
  ATTRIBUTES,
  attributeRow,
  key,
  knobNumber,
  specOf,
  tableEntry,
  type AdjustedLaunch,
} from "./adjusted";
import {
  attributeMetrics,
  exactTableContext,
  finalEvent,
  modelRollup,
  TableEvaluator,
  tableRollups,
  type Evaluated,
  type KeyCounts,
} from "./evaluate";
import { baseRunId, dataflowJobId, isoMicros } from "./ids";
import { mockConfigDigest, mockSnapshot, mockValues } from "./record";
import { EMBEDDER_URIS, MODEL_URIS, type EvalSpec } from "./storyline";
import { synthesize } from "./synth";
import { LANDING_DATASET, landingFqn, PROJECT, SOURCE_DATASET, sourceFqn, type Row, type TableDef } from "./thelook";

export const FOREST_MODEL = "isolated_forest_demo";
const FOREST_URI = `gs://synthetic-platform-demo/synthetic/relationships/${FOREST_MODEL}.yaml`;
/** `launch.max_tables_per_job` of the launch: at most this many tables per Dataflow job (ADR 0045 D3). */
export const FOREST_BATCH_SIZE = 10;
const DECLARED = 120;
/** Rows per side of the samples the metrics are computed from (small: 112 tables are evaluated). */
const SAMPLE_ROWS = 200;

export interface ForestTable {
  name: string;
  /** 1-based: every rule below reads it. */
  index: number;
  enabled: boolean;
  pk: string[];
  /** `dataset.table` the launch reads instead of `<io.source_dataset>.<name>` (no project: the source dataset's). */
  sourceTable: string | null;
  /** Rows generated for this table instead of `generation.num_rows`; null = the default. */
  numRows: number | null;
  /** Rows on a repeated key in the landing table (streaming uniqueness counts them, removes none). */
  duplicateRows: number;
}

const tableName = (i: number) => `T_${String(i).padStart(3, "0")}`;
const pad2 = (n: number) => String(n).padStart(2, "0");

export const FOREST_TABLES: readonly ForestTable[] = Array.from({ length: DECLARED }, (_, k) => {
  const i = k + 1;
  return {
    name: tableName(i),
    index: i,
    enabled: i % 15 !== 0,
    pk: i % 3 !== 0 ? ["COL_001"] : [],
    sourceTable: i % 20 === 7 ? `example_archive.${tableName(i)}_V2` : null,
    numRows: i % 25 === 11 ? 5000 : null,
    // Three and one keys landed twice.
    duplicateRows: i === 4 ? 6 : i === 8 ? 2 : 0,
  };
});

const GENERATED = FOREST_TABLES.filter((t) => t.enabled);
const byName = new Map(FOREST_TABLES.map((t) => [t.name, t]));

/**
 * The batch planner's bins for this model (ADR 0045 D3): with no edge every table is its own
 * component, so first-fit-decreasing fills the bins in name order, ten at a time.
 */
export const FOREST_BATCHES: readonly (readonly string[])[] = Array.from(
  { length: Math.ceil(GENERATED.length / FOREST_BATCH_SIZE) },
  (_, b) => GENERATED.slice(b * FOREST_BATCH_SIZE, (b + 1) * FOREST_BATCH_SIZE).map((t) => t.name),
);

/** A table's run id in a batched launch (ADR 0045 Ruling 14): `<base>-bNN-MM-<table>`. */
export const forestRunId = (base: string, batch: number, position: number, table: string) =>
  `${base}-b${pad2(batch)}-${pad2(position)}-${table}`;

// A two-part source_table takes the source dataset's project (ADR 0045 Ruling 7).
const sourceOf = (t: ForestTable) => (t.sourceTable ? `${PROJECT}.${t.sourceTable}` : sourceFqn(t.name));

/** `isolated_forest_demo` in the generated relationships.json shape. */
export const FOREST_RELATIONSHIP_MODEL: RelationshipModel = {
  model: FOREST_MODEL,
  description:
    "invented model of 120 unrelated tables under generic aliases: no foreign key, every 15th table " +
    "disabled, every third with no primary key",
  source: "gui/packages/mock/src/forest.ts",
  sha12: "",
  generation_order: GENERATED.map((t) => t.name),
  tables: FOREST_TABLES.map((t) => ({
    name: t.name,
    pk: t.pk,
    identity: [],
    enabled: t.enabled,
    note: t.enabled ? "" : "enabled: false — skipped by tables: all",
    fk: [],
    // The RelationTable keys of ADR 0045 D2: written only when the model sets them, as the export does (Ruling 9).
    ...(t.sourceTable ? { source_table: t.sourceTable } : {}),
    ...(t.numRows ? { num_rows: t.numRows } : {}),
  })),
};
FOREST_RELATIONSHIP_MODEL.sha12 = sha256Hex(JSON.stringify(FOREST_RELATIONSHIP_MODEL.tables)).slice(0, 12);

/**
 * The Composer DAG's tables=all branch chains one evaluation over every batch that landed (ADR 0045 D6, D7):
 * the adjusted-keys launch's spec, in streaming uniqueness, over the 112 generated tables.
 */
export const FOREST_SPEC: EvalSpec = {
  ...specOf(43, "2026-09-20T08:14:52.381904Z", "chained", 52),
  numRows: 1_000_000,
  uniquenessMode: "streaming",
  tables: GENERATED.map((t) => t.name),
};
export const FOREST_EVALUATION = FOREST_SPEC.id;

/**
 * A batch job's Dataflow name as the Composer DAG's tables=all branch builds it (ADR 0045, `sdfb_batches.batch_job_name`):
 * its job name `synthetic-sdfb-model-v<version>-<model slug>` plus `-bNN` — never the batch's run id
 * `<base>-bNN`, which only its launch_config log carries. Version and slug as `registryBase` writes them
 * (42 characters: inside Dataflow's 63, so nothing is truncated). On Composer the provider appends
 * `-<8 hex>`; the mock omits it, as its single-table job names do.
 */
const batchJobName = (batch: number) =>
  `synthetic-sdfb-model-v0-5-3-${FOREST_SPEC.llm.replace(/[^a-z0-9]+/g, "-")}-b${pad2(batch)}`;

// ------------------------------------------------------------------ rows --

const sourceRowsOf = (t: ForestTable) => 200_000 + t.index * 1_000;
const rowsOf = (t: ForestTable) => t.numRows ?? FOREST_SPEC.numRows;

function tableDef(t: ForestTable): TableDef {
  const keyed = t.pk.length > 0;
  return {
    name: t.name,
    sourceRows: sourceRowsOf(t),
    role: "isolated",
    columns: [...(keyed ? [key("COL_001", "pk")] : []), ...ATTRIBUTES],
    row(rng, i) {
      const attributes = attributeRow(rng);
      const row: Row = keyed ? { COL_001: i + 1 } : {};
      return Object.assign(row, attributes);
    },
  };
}

/**
 * The table's encoding-plan digest: its name, its columns (a keyless table has no COL_001) and the
 * evaluator and catalogue versions. From the name alone T_029's would be sixteen decimal digits, a
 * card-shaped run the mock's sensitive-content check refuses (mock.schema-conformance.test.ts).
 */
function planDigest(t: ForestTable): string {
  const columns = tableDef(t).columns.map((c) => c.name);
  return sha256Hex(`${t.name}|${columns.join(",")}|0.2.0|1.0.0`).slice(0, 16);
}

/**
 * The launch's `values` (ADR 0044 D2) — the record's builder for the spec, then the whole-model keys
 * of ADR 0045: `io.source_dataset` / `io.landing_dataset`, and `launch` holds exactly its four settable
 * keys (the batch identity is the snapshot's `batch` block, never a value). `max_parallel_jobs` is 1,
 * the default: the Composer DAG's tables=all branch does not forward it (its cap is the Airflow Variable
 * SDFB_MAX_PARALLEL_JOBS, Ruling 13).
 */
function forestValues(): Record<string, Record<string, unknown>> {
  const values = mockValues(FOREST_SPEC, FOREST_SPEC.tables);
  return {
    ...values,
    io: {
      ...values.io,
      landing_table: null,
      reference_table: null,
      source_dataset: `${PROJECT}.${SOURCE_DATASET}`,
      landing_dataset: `${PROJECT}.${LANDING_DATASET}`,
    },
    launch: { tables: "all", max_tables_per_job: FOREST_BATCH_SIZE, max_parallel_jobs: 1, prep_workers: 8 },
    relational: { ...values.relational, relationships_uri: FOREST_URI, generate_fk_relationships: "true" },
  };
}

/** `TableRelations.model_dump(mode="json")` of a table: ADR 0045's two keys always, null when unset (Ruling 9). */
const declaredOf = (t: ForestTable) => ({
  pk: t.pk,
  identity: [],
  fk: [],
  enabled: t.enabled,
  note: "",
  source_table: t.sourceTable,
  num_rows: t.numRows,
});

/**
 * One batch job's snapshot: the launch's values, this job's tables as declared, its batch block, and the
 * Dataflow region in its beam block (`run_pipeline.beam_block`): the tables=all branch's evaluation, by `--run_id`,
 * records no region, so a job link reads it here.
 */
function batchSnapshot(values: Record<string, Record<string, unknown>>, b: number, region: string): GeneratorSnapshot {
  const tables = FOREST_BATCHES[b]!;
  const snapshot = mockSnapshot(FOREST_SPEC, values);
  return {
    ...snapshot,
    beam: { ...snapshot.beam, region },
    relationships: {
      uri: FOREST_URI,
      model: FOREST_MODEL,
      sha12: FOREST_RELATIONSHIP_MODEL.sha12,
      tables: Object.fromEntries(tables.map((name) => [name, declaredOf(byName.get(name)!)])),
    },
    batch: { index: b, count: FOREST_BATCHES.length, tables: [...tables] },
  };
}

/** Every generated table's `validation_runs` row (as `build_run_summary` folds it), with the record columns. */
function forestRuns(
  base: string,
  jobIds: readonly string[],
  values: Record<string, Record<string, unknown>>,
  startMs: number,
  finishedAt: string,
  generatorVersion: string,
  region: string,
): ValidationRunsRow[] {
  const spec = FOREST_SPEC;
  const gate = knobNumber(`blocker_failure_ratio_${spec.env}`, 0.2);
  const digest = mockConfigDigest(values);
  return FOREST_BATCHES.flatMap((tables, b) => {
    const config = JSON.stringify(batchSnapshot(values, b, region));
    return tables.map((name, j): ValidationRunsRow => {
      const t = byName.get(name)!;
      const rows = rowsOf(t);
      // Streaming uniqueness: pk.duplicate is the rows beyond the first per key, counted, none removed.
      const duplicates = t.duplicateRows / 2;
      const byRule: Record<string, number> = duplicates ? { "pk.duplicate": duplicates } : {};
      const observed = duplicates / (rows + duplicates);
      return {
        ...EMPTY_RECORD_COLUMNS,
        run_id: forestRunId(base, b, j, name),
        reference_digest: sha256Hex(`${sourceOf(t)}|${spec.referenceRowsLimit}|${base}`),
        reference_table: sourceOf(t),
        landing_table: landingFqn(name),
        engine: spec.engine,
        model_uri: MODEL_URIS[spec.llm],
        env: spec.env,
        num_rows_requested: rows,
        valid_count: rows,
        dlq_count: duplicates,
        blocker_count: duplicates,
        dlq_by_rule: JSON.stringify(byRule),
        blocker_failure_ratio: gate,
        observed_blocker_ratio: observed,
        status: observed > gate ? "FAILED_BLOCKER" : "PASSED",
        excluded_blocker_rules: "",
        source_repeat_share: null,
        landing_repeat_share: null,
        repeat_share_delta: null,
        repeat_share_within_tolerance: null,
        // Each batch's summaries land as its job ends, six minutes apart, inside the generation window.
        created_at: isoMicros(Date.parse(finishedAt) - (FOREST_BATCHES.length - b) * 360_000 + j * 10_000, 100 + j),
        job_id: jobIds[b]!,
        job_name: batchJobName(b),
        launch_run_id: base,
        generator_version: generatorVersion,
        launched_at: isoMicros(startMs + b * 300_000, 7),
        embedder_uri: EMBEDDER_URIS[spec.embedder],
        retrieval_method: spec.retrieval,
        similarity: spec.similarity,
        seed: spec.seed,
        source_stats: spec.sourceStatsTier,
        relationships_uri: FOREST_URI,
        relationships_digest: FOREST_RELATIONSHIP_MODEL.sha12,
        config_digest: digest,
        config,
      };
    });
  });
}

type TableEntry = EvaluationDataHistoryRow["tables"][number];

/** Every generated table's metric rows, from generated samples; keys from the rules above. */
function evaluate(entries: readonly TableEntry[], seed: number): Evaluated {
  const spec = FOREST_SPEC;
  const out: Evaluated = { metrics: [], profiles: [], flags: [], scores: new Map() };
  for (const entry of entries) {
    const t = byName.get(entry.name!)!;
    const table = tableDef(t);
    const sourceRng = new Random(seedFrom("forest-source", t.name));
    const source = Array.from({ length: SAMPLE_ROWS }, (_, i) => table.row(sourceRng, i));
    const reference = source.slice(0, SAMPLE_ROWS / 2);
    const holdout = source.slice(SAMPLE_ROWS / 2);
    const synthetic = synthesize({
      table,
      source,
      reference,
      spec,
      pools: new Map(),
      rows: SAMPLE_ROWS,
      parentKeys: new Map(),
    });
    const samples = { source, synthetic, reference, holdout };
    const ev = new TableEvaluator(
      exactTableContext(spec, table, entry, samples, new Random(seedFrom(seed, spec.id, t.name))),
    );
    const detectionShift = attributeMetrics(ev, ATTRIBUTE_PAIRS);
    const rows = entry.rows_synthetic!;
    // A table with no declared key: the evaluator's not-evaluated PK row (evaluate.ts tableLevel).
    const keys: KeyCounts = t.pk.length
      ? { columns: t.pk, duplicateRows: t.duplicateRows, distinctKeys: rows - t.duplicateRows / 2 }
      : { columns: [], duplicateRows: 0, distinctKeys: 0 };
    ev.tableLevel(detectionShift, keys);
    out.scores.set(t.name, tableRollups(ev));
    out.metrics.push(...ev.result.metrics);
    out.profiles.push(...ev.result.profiles);
    out.flags.push(...ev.result.flags);
  }
  // The model roll-up, on the first table's context (no rows of its own).
  const first = entries[0]!;
  out.metrics.push(
    ...modelRollup(
      spec,
      tableDef(byName.get(first.name!)!),
      first.encoding_plan_digest!,
      FOREST_MODEL,
      [...out.scores.values()],
      new Random(seedFrom(seed, spec.id, "model")),
    ),
  );
  return out;
}

/** The launch's rows: the same tables as the adjusted-keys launch's. */
export type ForestLaunch = AdjustedLaunch;

/** `registryBase` is the storyline's RUNNING-event builder (dataset.ts), as for the adjusted-keys launch. */
export function buildForestLaunch(
  seed: number,
  registryBase: (spec: EvalSpec, rng: Random) => EvaluationDataHistoryRow,
): ForestLaunch {
  const spec = FOREST_SPEC;
  const rng = new Random(seedFrom(seed, spec.id));
  const running = registryBase(spec, rng);
  const base = baseRunId(spec);
  // The launch's own window and region: its batch jobs ran in them, whatever the evaluation records.
  const startMs = Date.parse(running.generation_started_at!);
  const finishedAt = running.generation_finished_at!;
  const region = running.generation_region!;
  const jobIds = FOREST_BATCHES.map((_, b) => {
    const jobRng = new Random(seedFrom("forest-job", b));
    return dataflowJobId(startMs + b * 300_000, (lo, hi) => jobRng.int(lo, hi));
  });
  const values = forestValues();
  const runs = forestRuns(base, jobIds, values, startMs, finishedAt, running.generator_version!, region);
  running.relationship_model = FOREST_MODEL;
  running.relationship_model_sha = FOREST_RELATIONSHIP_MODEL.sha12;
  running.relationship_model_uri = FOREST_URI;
  running.model_adjusted = false;
  running.base_run_id = base;
  running.run_ids = runs.map((r) => r.run_id);
  // `--run_id <base> --generation_job_ids <the 12 landed jobs>` (ADR 0045): the registry records every
  // one, the first as generation_job_id — registryBase's single-job default would be stale. The evaluation
  // resolves with no job (sdfb_evaluation/context/launch.py), so it records no job name, region or window.
  running.generation_job_id = jobIds[0]!;
  running.generation_job_ids = [...jobIds];
  running.generation_job_name = null;
  running.generation_region = null;
  running.generation_started_at = null;
  running.generation_finished_at = null;
  running.generator_config_digest = mockConfigDigest(values);
  // The record supplied the parameters (ADR 0044 D5): the snapshot's values as flags, without
  // landing_table / reference_table, with the whole-model keys of ADR 0045.
  running.params_source = "validation_runs";
  const flat = running.generation_params;
  const {
    landing_table: _landing,
    reference_table: _reference,
    ...params
  } = flat && typeof flat === "object" && !Array.isArray(flat) ? flat : {};
  running.generation_params = {
    ...params,
    tables: "all",
    max_tables_per_job: FOREST_BATCH_SIZE,
    max_parallel_jobs: 1,
    prep_workers: 8,
    source_dataset: `${PROJECT}.${SOURCE_DATASET}`,
    landing_dataset: `${PROJECT}.${LANDING_DATASET}`,
    relationships_uri: FOREST_URI,
  };
  running.tables = FOREST_BATCHES.flatMap((tables, b) =>
    tables.map((name, j) => {
      const t = byName.get(name)!;
      const run = runs.find((r) => r.run_id === forestRunId(base, b, j, name))!;
      // No edge to or from it: the evaluator's `isolated` role.
      const facts = {
        name,
        role: "isolated" as const,
        sourceTable: sourceOf(t),
        sourceRows: sourceRowsOf(t),
        rows: rowsOf(t),
      };
      // scope=manual: the Composer DAG's tables=all branch reads each landing table whole (ADR 0045 D7).
      return {
        ...tableEntry(spec, facts, run.run_id, run.reference_digest!, true),
        encoding_plan_digest: planDigest(t),
      };
    }),
  );

  const evaluated = evaluate(running.tables, seed);
  const final = finalEvent(spec, running, evaluated);
  final.warnings = [];
  final.status = "SUCCEEDED";
  final.status_reason = null;
  return {
    registry: [running, final],
    metrics: evaluated.metrics,
    profiles: evaluated.profiles,
    flags: evaluated.flags,
    validationRuns: runs,
  };
}
