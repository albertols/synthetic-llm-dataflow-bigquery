/**
 * Test fixtures for the RAG tab (imported by *.test.tsx only): a small,
 * real hashing-384 cloud of GReaT row documents and a RagModel built around
 * it, with the PCA and strategies computed by the same code the worker runs.
 */
import type { ChunkMeta, RagSet } from "@contracts/api";
import { hashingEmbed, serializeGreat, sha256Hex } from "@synthetic-platform/stats";

import { runPca, sphericalKMeans } from "./lib/projection";
import type { SpaceId } from "./lib/selection";
import { computeStrategies } from "./lib/strategyJob";
import { buildCloud } from "./lib/useCloud";
import type { RagModel } from "./useRagModel";

export const COLUMNS = ["id", "first_name", "city", "country"] as const;
const CITIES = ["Port Pine", "New Elm", "Old Oak", "Fort Ash"];
const COUNTRIES = ["Spain", "Brasil", "Japan"];
const NAMES = ["Alvin", "Bea", "Cato", "Dara", "Elio", "Fay"];

export function rowDocTexts(n = 36): string[] {
  return Array.from({ length: n }, (_, i) =>
    serializeGreat(
      { id: i + 1, first_name: NAMES[i % NAMES.length]!, city: CITIES[i % CITIES.length]!, country: COUNTRIES[i % 3]! },
      [...COLUMNS],
    ),
  );
}

export const FIXTURE_SET: RagSet = {
  source_fqn: "demo-project.synthetic_source.users",
  reference_digest: sha256Hex("fixture"),
  embedder_id: "hashing-384",
  embedder_version: "v1",
  row_docs: 36,
  value_chunks: 0,
  columns: [],
  dim: 384,
  created_at: "2026-09-02T11:10:00.000000Z",
};

export function rowDocEnvelope(texts = rowDocTexts()) {
  const meta: ChunkMeta[] = texts.map((text, i) => ({
    chunk_id: sha256Hex(`c${i}`),
    source_fqn: FIXTURE_SET.source_fqn,
    chunk_kind: "row_doc",
    chunk_index: 0,
    chunk_text: text,
    column: null,
    row_digest: sha256Hex(text),
    embedder_id: "hashing-384",
    embedder_version: "v1",
  }));
  const vectors = new Float32Array(texts.length * 384);
  texts.forEach((t, i) => vectors.set(hashingEmbed(t), i * 384));
  return { meta, vectors, dim: 384, count: texts.length };
}

let base: ReturnType<typeof computeBase> | null = null;

function computeBase() {
  const space: SpaceId = "rows";
  const cloud = buildCloud("fixture", space, [rowDocEnvelope()]);
  const layout = runPca(cloud.vectors, cloud.dim);
  const strategies = computeStrategies(cloud.vectors, cloud.dim, 8, 0, "kcenter");
  const pca = {
    status: "done" as const,
    result: layout,
    clusters: sphericalKMeans(cloud.vectors, cloud.dim),
    trust: 0.42,
  };
  return { space, cloud, strategies, pca };
}

export function fixtureModel(
  overrides: { search?: Partial<RagModel["search"]>; setSearch?: RagModel["setSearch"] } = {},
): RagModel {
  base ??= computeBase();
  const { space, cloud, strategies, pca } = base;
  const model = {
    search: { ...overrides.search },
    setSearch: overrides.setSearch ?? (() => {}),
    facets: { isPending: false, error: null },
    sets: [FIXTURE_SET],
    resolved: { table: "users", samples: [], set: FIXTURE_SET, space, notices: [] },
    set: FIXTURE_SET,
    cloudQuery: { error: null, data: { cloud, bytesEstimate: null, dataSource: "mock" } },
    cloud,
    method: "pca",
    projection: { pca, umap: null, active: pca, strategies: { status: "done", data: strategies } },
    strategy: "centroid",
    k: 8,
    attempt: 0,
    retrieves: true,
    seeds: strategies.rows.find((r) => r.id === "centroid")!.picks,
  };
  return model as unknown as RagModel;
}
