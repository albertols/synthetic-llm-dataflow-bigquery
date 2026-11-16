/**
 * The generation record of a mock launch (ADR 0044), derived from its EvalSpec with no random draw, so
 * every seeded number of the dataset keeps its value. The registry rows keep their flat
 * `generation_params` even for a launch that has a record (no seeded number may move); the record's
 * `values` carry the same parameters, grouped by the generator's sections.
 */
import type { GeneratorSnapshot } from "@synthetic-platform/contracts";
import { sha256Hex } from "@synthetic-platform/stats";

import { EMBEDDER_URIS, MODEL_URIS, type EvalSpec } from "./storyline";
import { landingFqn, MOCK_RELATIONSHIP_MODEL, RELATIONSHIP_MODEL, sourceFqn } from "./thelook";

export const MOCK_GENERATOR_BASE = "0.6.1";

export const RELATIONSHIPS_URI = `gs://synthetic-platform-demo/synthetic/relationships/${RELATIONSHIP_MODEL}.yaml`;

/** What a launch's record is derived from. */
export type RecordSpec = Pick<
  EvalSpec,
  | "index"
  | "id"
  | "env"
  | "runner"
  | "engine"
  | "llm"
  | "embedder"
  | "retrieval"
  | "seed"
  | "similarity"
  | "referenceRowsLimit"
  | "numRows"
  | "uniquenessMode"
  | "freetextExpansion"
  | "sourceStatsTier"
  | "vllmDtype"
  | "relational"
>;

type Values = Record<string, Record<string, unknown>>;

/** The Dataflow job name a launch runs under. */
export const mockGenerationJobName = (spec: Pick<EvalSpec, "llm">) =>
  `synthetic-sdfb-v0-5-3-${spec.llm.replace(/[^a-z0-9]+/g, "-")}`.slice(0, 63);

export function mockVersions(spec: Pick<EvalSpec, "id" | "index">): { launcher: string; worker: string } {
  const worker = `${MOCK_GENERATOR_BASE}-master-${sha256Hex(spec.id).slice(0, 7)}`;
  // One launch in eleven was launched from a dirty checkout: its launcher is not its workers' image.
  return { launcher: spec.index % 11 === 5 ? `${worker}-dirty` : worker, worker };
}

/** The launch's effective configuration, grouped by the generator's sections. */
export function mockValues(spec: RecordSpec, tables: readonly string[]): Values {
  return {
    io: {
      landing_table: tables.map((t) => landingFqn(t)).join(","),
      reference_table: sourceFqn(tables[tables.length - 1]!),
      env: spec.env,
    },
    sampling: { reference_rows_limit: spec.referenceRowsLimit, source_stats: spec.sourceStatsTier },
    generation: {
      engine: spec.engine,
      similarity: spec.similarity,
      seed: spec.seed === "derived" ? "" : spec.seed,
      num_rows: spec.numRows,
      uniqueness_mode: spec.uniquenessMode,
    },
    free_text: { pool_seed_strategy: spec.retrieval, freetext_expansion: spec.freetextExpansion },
    rag: { embedder_uri: EMBEDDER_URIS[spec.embedder] },
    guardrails: { repeat_share_tolerance: 0.05 },
    relational: { relationships_uri: spec.relational ? RELATIONSHIPS_URI : "" },
    serving: { model_uri: MODEL_URIS[spec.llm], vllm_dtype: spec.vllmDtype },
  };
}

export function mockSnapshot(spec: RecordSpec, values: Values): GeneratorSnapshot {
  const overridden = spec.index % 7 === 3;
  const provenance: Record<string, string> = {};
  for (const [section, block] of Object.entries(values))
    for (const key of Object.keys(block)) provenance[`${section}.${key}`] = "flag";
  if (overridden) provenance["generation.similarity"] = "file";
  return {
    config_version: 1,
    values,
    provenance,
    overridden: overridden ? [{ key: "generation.similarity", typed: 0.5, file: values.generation!.similarity }] : [],
    derived: { temperature_ladder: [0.7, 1.0, 1.3], profiler_version: "2", cache_keys: {} },
    thresholds: { uri: "config/thresholds.yml", env: spec.env },
    relationships: spec.relational
      ? {
          uri: RELATIONSHIPS_URI,
          model: RELATIONSHIP_MODEL,
          sha12: MOCK_RELATIONSHIP_MODEL.sha12,
          tables: Object.fromEntries(MOCK_RELATIONSHIP_MODEL.tables.map((t) => [t.name, t])),
        }
      : { uri: null, model: null, sha12: null, tables: {} },
    beam: { runner: spec.runner },
    launcher: { version: mockVersions(spec).launcher },
    batch: null,
  };
}

export function mockConfigDigest(values: Values): string {
  return sha256Hex(JSON.stringify(values)).slice(0, 12);
}
