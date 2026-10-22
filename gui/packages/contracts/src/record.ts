/**
 * The generation record (ADR 0044): fourteen NULLABLE columns of `validation_runs` that say what
 * generated a run. A row written before them, or into a table without them, has every one null.
 */
import type { EvaluationDataHistoryRow, ValidationRunsRow } from "../generated/schemas";

export const RECORD_COLUMNS = [
  "job_id",
  "job_name",
  "launch_run_id",
  "generator_version",
  "launched_at",
  "embedder_uri",
  "retrieval_method",
  "similarity",
  "seed",
  "source_stats",
  "relationships_uri",
  "relationships_digest",
  "config_digest",
  "config",
] as const;

export type RecordColumn = (typeof RECORD_COLUMNS)[number];

/** A `validation_runs` row with no generation record. */
export const EMPTY_RECORD_COLUMNS: Pick<ValidationRunsRow, RecordColumn> = {
  job_id: null,
  job_name: null,
  launch_run_id: null,
  generator_version: null,
  launched_at: null,
  embedder_uri: null,
  retrieval_method: null,
  similarity: null,
  seed: null,
  source_stats: null,
  relationships_uri: null,
  relationships_digest: null,
  config_digest: null,
  config: null,
};

/** `validation_runs.config` parsed. Every block is optional; a newer generator's extra keys pass through. */
export interface GeneratorSnapshot {
  config_version?: number;
  values?: Record<string, Record<string, unknown>>;
  provenance?: Record<string, string>;
  overridden?: { key: string; typed: unknown; file: unknown }[];
  derived?: { cache_keys?: Record<string, string | null>; [key: string]: unknown };
  thresholds?: Record<string, unknown>;
  relationships?: {
    uri?: string | null;
    model?: string | null;
    sha12?: string | null;
    tables?: Record<string, unknown>;
    [key: string]: unknown;
  };
  beam?: Record<string, unknown>;
  launcher?: { version?: string; [key: string]: unknown };
  batch?: unknown;
  [key: string]: unknown;
}

export type RecordRun = Pick<
  ValidationRunsRow,
  | "run_id"
  | "landing_table"
  | "job_id"
  | "job_name"
  | "launch_run_id"
  | "generator_version"
  | "launched_at"
  | "config_digest"
>;

export interface GenerationRecord {
  evaluation_id: string;
  /** This launch's rows, one per run id (the newest), in run-id order. */
  runs: RecordRun[];
  snapshot: GeneratorSnapshot | null;
  snapshot_run_id: string | null;
  launcher_version: string | null;
  worker_versions: string[];
  version_mismatch: boolean;
  config_digests: string[];
  /**
   * Every table the launch's rows declare, unioned over each row's snapshot (a batched launch writes
   * one snapshot per job, each holding only that job's tables), by name.
   */
  declared_tables: DeclaredTable[];
  /** One entry per batch job whose rows carry a `batch` block, by index; [] for a launch in one job. */
  batches: RecordBatch[];
}

type LaunchKeys = Pick<EvaluationDataHistoryRow, "evaluation_id" | "run_ids" | "base_run_id" | "generation_job_id"> &
  Partial<Pick<EvaluationDataHistoryRow, "generation_job_ids">>;

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const distinct = (values: readonly (string | null)[]): string[] =>
  [...new Set(values.filter((v): v is string => !!v))].sort();

/**
 * The snapshot of a `config` column, or null when it is absent, not a JSON object, or carries a `values` that is
 * not an object. `overridden` keeps only its entries that are objects with a string `key` (dropped when it is not
 * a list). Only the generator writes `config`, but a reader never trusts its block types (ADR 0044).
 */
export function parseSnapshot(text: string | null | undefined): GeneratorSnapshot | null {
  if (!text) return null;
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isObject(value) || ("values" in value && !isObject(value.values))) return null;
  if (!("overridden" in value)) return value;
  const { overridden, ...rest } = value;
  if (!Array.isArray(overridden)) return rest;
  return { ...rest, overridden: overridden.filter((o) => isObject(o) && typeof o.key === "string") };
}

// ------------------------------------------------- whole-model launches (ADR 0045) --

/** A foreign key as the launch declared it (`TableRelations.fk[]`). */
export interface DeclaredEdge {
  cols: string[];
  ref: string;
  ref_cols: string[];
  enforced: boolean;
}

/**
 * A table as the launch declared it: the snapshot's `relationships.tables[name]`
 * (`TableRelations.model_dump(mode="json")`, ADR 0045 D2). Since ADR 0045 every row carries
 * `source_table` and `num_rows` (null when unset); rows written before it have neither, and read as
 * null. Tolerant: any other key a generator does not write reads as its default.
 */
export interface DeclaredTable {
  name: string;
  pk: string[];
  identity: string[];
  fk: DeclaredEdge[];
  /** null: the snapshot does not say. */
  enabled: boolean | null;
  /** `[project.]dataset.table` the launch read instead of `<io.source_dataset>.<name>`; null = the default. */
  source_table: string | null;
  /** Rows the launch generated for this table instead of `generation.num_rows`; null = the default. */
  num_rows: number | null;
}

/** One Dataflow job of a whole-model launch: the snapshot's `batch` block and the rows that carry it. */
export interface RecordBatch {
  index: number;
  /** How many batch jobs the launch planned (`batch.count`). */
  count: number;
  /** This job's tables, as its snapshot lists them. */
  tables: string[];
  job_ids: string[];
  run_ids: string[];
}

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];

/** `relationships.tables` of a snapshot, by name; entries that are not objects are skipped. */
export function parseDeclaredTables(snapshot: GeneratorSnapshot | null): DeclaredTable[] {
  const tables = snapshot?.relationships?.tables;
  if (!isObject(tables)) return [];
  const out: DeclaredTable[] = [];
  for (const [name, raw] of Object.entries(tables)) {
    if (!isObject(raw)) continue;
    const fk = (Array.isArray(raw.fk) ? raw.fk : [])
      .filter(isObject)
      .map((e) => ({
        cols: strings(e.cols),
        ref: typeof e.ref === "string" ? e.ref : "",
        ref_cols: strings(e.ref_cols),
        enforced: e.enforced !== false,
      }))
      .filter((e) => e.ref !== "" && e.cols.length > 0);
    const rows = raw.num_rows;
    out.push({
      name,
      pk: strings(raw.pk),
      identity: strings(raw.identity),
      fk,
      enabled: typeof raw.enabled === "boolean" ? raw.enabled : null,
      source_table: typeof raw.source_table === "string" && raw.source_table !== "" ? raw.source_table : null,
      num_rows: typeof rows === "number" && Number.isInteger(rows) && rows >= 1 ? rows : null,
    });
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

/** The snapshot's `batch` block (`{index, count, tables}`), or null (`batch: null`, absent or malformed). */
export function parseBatch(
  snapshot: GeneratorSnapshot | null,
): { index: number; count: number; tables: string[] } | null {
  const batch = snapshot?.batch;
  if (!isObject(batch)) return null;
  const { index, count } = batch;
  if (typeof index !== "number" || !Number.isInteger(index) || index < 0) return null;
  if (typeof count !== "number" || !Number.isInteger(count) || count <= index) return null;
  return { index, count, tables: strings(batch.tables) };
}

/**
 * The evaluation's Dataflow jobs: `generation_job_ids` (ADR 0045 — every batch job that landed), else, for a
 * registry row written before that column (it reads `[]`), the one `generation_job_id`.
 */
function launchJobs(e: Pick<LaunchKeys, "generation_job_id" | "generation_job_ids">): Set<string> {
  const listed = e.generation_job_ids ?? [];
  if (listed.length) return new Set(listed);
  return new Set(e.generation_job_id ? [e.generation_job_id] : []);
}

/** A row of this evaluation's launch: one of its job ids, a run id it lists, or its base run id. */
export function isLaunchRow(
  row: Pick<ValidationRunsRow, "run_id" | "job_id">,
  e: Omit<LaunchKeys, "evaluation_id">,
): boolean {
  if (row.job_id && (row.job_id === e.generation_job_id || !!e.generation_job_ids?.includes(row.job_id))) return true;
  if (e.run_ids.includes(row.run_id)) return true;
  const base = e.base_run_id;
  return !!base && (row.run_id === base || row.run_id.startsWith(`${base}-`));
}

/**
 * The record an evaluation's generation wrote, from the `validation_runs` rows `runs.forEvaluation` returned
 * (newest first). Rows of another launch are dropped, and — once any row names one of the evaluation's Dataflow
 * jobs (`launchJobs`) — so is a row that names a job it does not list, even under a run id the evaluation lists:
 * a reused `--run_id`, or a whole-model launch's batch job that FAILED after writing its rows and so was not
 * evaluated (ADR 0045; the evaluator's `rows_of_jobs` rule). A row with no job id stays. A single-job evaluation
 * is a set of one. The first snapshot in run-id order is the launch's (every table of one job carries the same
 * one; a batched launch's is batch 0's); `declared_tables` and `batches` read every row's.
 */
export function buildGenerationRecord(evaluation: LaunchKeys, rows: readonly ValidationRunsRow[]): GenerationRecord {
  const jobs = launchJobs(evaluation);
  const candidates = rows.filter((row) => isLaunchRow(row, evaluation));
  const ownJob = candidates.some((row) => !!row.job_id && jobs.has(row.job_id));
  const seen = new Set<string>();
  const latest: ValidationRunsRow[] = [];
  for (const row of candidates) {
    if (ownJob && row.job_id && !jobs.has(row.job_id)) continue;
    if (seen.has(row.run_id)) continue;
    seen.add(row.run_id);
    latest.push(row);
  }
  latest.sort((a, b) => a.run_id.localeCompare(b.run_id));
  let snapshot: GeneratorSnapshot | null = null;
  let snapshotRunId: string | null = null;
  for (const row of latest) {
    const parsed = parseSnapshot(row.config);
    if (parsed) {
      snapshot = parsed;
      snapshotRunId = row.run_id;
      break;
    }
  }
  const launcher = snapshot?.launcher?.version;
  const launcherVersion = typeof launcher === "string" ? launcher : null;
  const workerVersions = distinct(latest.map((r) => r.generator_version));
  // Whole-model launches (ADR 0045): every row's snapshot, not only the first one's — each batch job's
  // snapshot holds that job's tables and its `batch` block.
  const declared = new Map<string, DeclaredTable>();
  const batches = new Map<number, RecordBatch>();
  for (const row of latest) {
    const parsed = parseSnapshot(row.config);
    if (!parsed) continue;
    for (const table of parseDeclaredTables(parsed)) if (!declared.has(table.name)) declared.set(table.name, table);
    const batch = parseBatch(parsed);
    if (!batch) continue;
    const entry = batches.get(batch.index) ?? { ...batch, job_ids: [], run_ids: [] };
    if (row.job_id && !entry.job_ids.includes(row.job_id)) entry.job_ids.push(row.job_id);
    entry.run_ids.push(row.run_id);
    batches.set(batch.index, entry);
  }
  return {
    evaluation_id: evaluation.evaluation_id,
    runs: latest.map((r) => ({
      run_id: r.run_id,
      landing_table: r.landing_table,
      job_id: r.job_id,
      job_name: r.job_name,
      launch_run_id: r.launch_run_id,
      generator_version: r.generator_version,
      launched_at: r.launched_at,
      config_digest: r.config_digest,
    })),
    snapshot,
    snapshot_run_id: snapshotRunId,
    launcher_version: launcherVersion,
    worker_versions: workerVersions,
    version_mismatch: launcherVersion !== null && workerVersions.some((v) => v !== launcherVersion),
    config_digests: distinct(latest.map((r) => r.config_digest)),
    declared_tables: [...declared.values()].sort((a, b) => a.name.localeCompare(b.name)),
    batches: [...batches.values()].sort((a, b) => a.index - b.index),
  };
}

/** `values` as dotted keys (`generation.similarity`). */
export function flattenValues(snapshot: GeneratorSnapshot | null): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const values = snapshot?.values;
  if (!isObject(values)) return out;
  for (const [section, block] of Object.entries(values)) {
    if (!isObject(block)) continue;
    for (const [key, value] of Object.entries(block)) out[`${section}.${key}`] = value;
  }
  return out;
}

export interface ConfigDiffRow {
  key: string;
  /** One per snapshot, in the order given (undefined: the key is not in that snapshot). */
  values: unknown[];
  provenance: (string | null)[];
}

/** The keys whose values differ between the snapshots that exist; nothing with fewer than two. */
export function configDiff(snapshots: readonly (GeneratorSnapshot | null)[]): ConfigDiffRow[] {
  if (snapshots.filter((s) => s !== null).length < 2) return [];
  const flat = snapshots.map(flattenValues);
  const keys = [...new Set(flat.flatMap((f) => Object.keys(f)))].sort();
  const rows: ConfigDiffRow[] = [];
  for (const key of keys) {
    const values = flat.map((f) => f[key]);
    const shown = values.filter((_, i) => snapshots[i] !== null).map((v) => JSON.stringify(v ?? null));
    if (new Set(shown).size < 2) continue;
    rows.push({
      key,
      values,
      provenance: snapshots.map((s) => {
        const origin = s?.provenance?.[key];
        return typeof origin === "string" ? origin : null;
      }),
    });
  }
  return rows;
}

/** `<base>-<branch>-<sha7>[-dirty]` (sdfb_core/version.py). */
export const GENERATOR_VERSION_RE = /^(?<base>[^-]+)-(?<branch>.+)-(?<sha>[0-9a-f]{7}|unknown)(?<dirty>-dirty)?$/;

export function parseGeneratorVersion(
  version: string | null | undefined,
): { base: string; branch: string; sha: string; dirty: boolean } | null {
  const match = version ? GENERATOR_VERSION_RE.exec(version) : null;
  if (!match?.groups) return null;
  const { base, branch, sha, dirty } = match.groups;
  return { base: base!, branch: branch!, sha: sha!, dirty: !!dirty };
}
