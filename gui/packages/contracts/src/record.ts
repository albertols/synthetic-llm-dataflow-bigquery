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
}

type LaunchKeys = Pick<EvaluationDataHistoryRow, "evaluation_id" | "run_ids" | "base_run_id" | "generation_job_id">;

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const distinct = (values: readonly (string | null)[]): string[] =>
  [...new Set(values.filter((v): v is string => !!v))].sort();

/** The snapshot of a `config` column, or null when it is absent or not a JSON object. */
export function parseSnapshot(text: string | null | undefined): GeneratorSnapshot | null {
  if (!text) return null;
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  return isObject(value) ? value : null;
}

/** A row of this evaluation's launch: its job id, a run id it lists, or its base run id. */
export function isLaunchRow(
  row: Pick<ValidationRunsRow, "run_id" | "job_id">,
  e: Omit<LaunchKeys, "evaluation_id">,
): boolean {
  if (row.job_id && e.generation_job_id && row.job_id === e.generation_job_id) return true;
  if (e.run_ids.includes(row.run_id)) return true;
  const base = e.base_run_id;
  return !!base && (row.run_id === base || row.run_id.startsWith(`${base}-`));
}

/**
 * The record an evaluation's generation wrote, from the `validation_runs` rows `runs.forEvaluation` returned
 * (newest first). Rows of another launch are dropped; the first snapshot in run-id order is the launch's
 * (every table of one job carries the same one).
 */
export function buildGenerationRecord(evaluation: LaunchKeys, rows: readonly ValidationRunsRow[]): GenerationRecord {
  const seen = new Set<string>();
  const latest: ValidationRunsRow[] = [];
  for (const row of rows) {
    if (seen.has(row.run_id) || !isLaunchRow(row, evaluation)) continue;
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
