/**
 * The generation record (ADR 0044): fourteen NULLABLE columns of `validation_runs` that say what
 * generated a run. A row written before them, or into a table without them, has every one null.
 */
import type { ValidationRunsRow } from "../generated/schemas";

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
