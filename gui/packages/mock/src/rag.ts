/**
 * `rag_chunks`: row documents (the first 1,024 reference rows, GReaT text) and
 * deduplicated free-text value chunks, per (source table, reference digest,
 * embedder), with Float32 embeddings.
 *
 * - `hashing-384/v1`: the real HashingEmbedder port over the chunk text, so the
 *   RAG tab's query box (which embeds with the same port) lands in the same space.
 * - `bge-small-en-v1.5/v1`: no model in the browser; vectors come from a seeded
 *   vMF-like mixture (normalized Gaussians around 8 cluster centres keyed by a
 *   semantic field), which gives the explorer a clustered cloud to show.
 *
 * `chunk_id` uses SHA-256 where the pipeline uses blake2b-256 (the same 64-hex shape).
 */
import type { RagChunksRow } from "@synthetic-platform/contracts";
import { hashingEmbed, Random, seedFrom, serializeGreat, sha256Hex, type GreatValue } from "@synthetic-platform/stats";

import { referenceDigest } from "./ids";
import { sourceFqn, TABLES, type Row, type TableDef } from "./thelook";

export const MAX_ROW_DOC_ROWS = 1024;
export const MAX_FREE_TEXT_VALUES_PER_COLUMN = 1024;
export const EMBEDDING_DIM = 384;
const CLUSTERS = 8;

export interface ChunkMetaRow {
  chunk_id: string;
  source_fqn: string;
  source_pk: Record<string, unknown> | null;
  row_digest: string;
  reference_digest: string;
  chunk_index: number;
  chunk_kind: "row_doc" | "free_text_col";
  chunk_text: string;
  embedder_id: string;
  embedder_version: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface RagSet {
  source_fqn: string;
  reference_digest: string;
  embedder_id: string;
  embedder_version: string;
  dim: number;
  created_at: string;
  chunks: ChunkMetaRow[];
  /** chunks.length × dim, row-major Float32. */
  vectors: Float32Array;
}

/** Python's `json.dumps(row, sort_keys=True, default=str)` layout (", " and ": " separators). */
function canonicalJson(row: Record<string, unknown>): string {
  const keys = Object.keys(row).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}: ${JSON.stringify(row[k] ?? null)}`).join(", ")}}`;
}

const greatValue = (table: TableDef, column: string, value: Row[string]): GreatValue => {
  const def = table.columns.find((c) => c.name === column)!;
  if (value === null || value === undefined) return null;
  if (def.bqType === "TIMESTAMP") return { py: "datetime", value: String(value) };
  if (def.bqType === "FLOAT64" && typeof value === "number") return { py: "float", value };
  return value;
};

function vmfVectors(keys: readonly string[], seed: number): Float32Array {
  const rng = new Random(seed);
  const centres = Array.from({ length: CLUSTERS }, () => {
    const c = Array.from({ length: EMBEDDING_DIM }, () => rng.normal());
    const norm = Math.hypot(...c);
    return c.map((v) => v / norm);
  });
  const clusterOf = new Map<string, number>();
  const out = new Float32Array(keys.length * EMBEDDING_DIM);
  keys.forEach((key, i) => {
    if (!clusterOf.has(key)) clusterOf.set(key, clusterOf.size % CLUSTERS);
    const centre = centres[clusterOf.get(key)!]!;
    const v = centre.map((c) => c + rng.normal(0, 0.042));
    const norm = Math.hypot(...v);
    v.forEach((x, j) => (out[i * EMBEDDING_DIM + j] = x / norm));
  });
  return out;
}

function chunkSet(
  table: TableDef,
  rows: readonly Row[],
  digest: string,
  embedder: "hashing-384" | "bge-small-en-v1.5",
  createdAt: string,
): RagSet {
  const fqn = sourceFqn(table.name);
  const order = table.columns.map((c) => c.name);
  const floats = table.columns.filter((c) => c.bqType === "FLOAT64").map((c) => c.name);
  const pk = table.columns.filter((c) => c.role === "pk").map((c) => c.name);
  const chunks: ChunkMetaRow[] = [];
  const clusterKeys: string[] = [];
  const semantic = table.name === "users" ? "country" : table.name === "user_features" ? "feat_007" : "status";
  const base = {
    source_fqn: fqn,
    reference_digest: digest,
    embedder_id: embedder,
    embedder_version: "v1",
    created_at: createdAt,
  };
  for (const row of rows.slice(0, MAX_ROW_DOC_ROWS)) {
    const great: Record<string, GreatValue> = {};
    for (const column of order) great[column] = greatValue(table, column, row[column] ?? null);
    const rowDigest = sha256Hex(canonicalJson(row));
    chunks.push({
      ...base,
      chunk_id: sha256Hex(`${fqn}:${rowDigest}:0`),
      source_pk: pk.length ? Object.fromEntries(pk.map((c) => [c, row[c] ?? null])) : null,
      row_digest: rowDigest,
      chunk_index: 0,
      chunk_kind: "row_doc",
      chunk_text: serializeGreat(great, order, { floatColumns: floats }),
      metadata: {},
    });
    clusterKeys.push(`row:${String(row[semantic] ?? "")}`);
  }
  const freeText = table.columns.filter((c) => c.plan === "freetext_llm_pool" || c.plan === "shaped_identifier");
  for (const column of freeText) {
    const seen = new Set<string>();
    for (const row of rows) {
      const value = row[column.name];
      if (value === null || value === undefined || value === "") continue;
      const text = String(value);
      if (seen.has(text) || seen.size >= MAX_FREE_TEXT_VALUES_PER_COLUMN) continue;
      seen.add(text);
      const valueDigest = sha256Hex(canonicalJson({ column: column.name, value: text }));
      chunks.push({
        ...base,
        chunk_id: sha256Hex(`${fqn}:${valueDigest}:0`),
        source_pk: null,
        row_digest: valueDigest,
        chunk_index: 0,
        chunk_kind: "free_text_col",
        chunk_text: text,
        metadata: { column: column.name },
      });
      clusterKeys.push(`${column.name}:${text[0] ?? ""}`);
    }
  }
  let vectors: Float32Array;
  if (embedder === "hashing-384") {
    vectors = new Float32Array(chunks.length * EMBEDDING_DIM);
    chunks.forEach((chunk, i) => vectors.set(hashingEmbed(chunk.chunk_text), i * EMBEDDING_DIM));
  } else vectors = vmfVectors(clusterKeys, seedFrom("bge", fqn, digest));
  return { ...base, dim: EMBEDDING_DIM, chunks, vectors };
}

/** Which (table, sample size, tier, embedder) sets exist: the ones the storyline's launches populate. */
const SETS: [string, number, "sample" | "exact", "hashing-384" | "bge-small-en-v1.5", string][] = [
  ["users", 10_000, "sample", "hashing-384", "2026-08-03T04:50:00Z"],
  ["users", 10_000, "sample", "bge-small-en-v1.5", "2026-08-04T07:20:00Z"],
  ["orders", 10_000, "sample", "hashing-384", "2026-08-03T04:55:00Z"],
  ["order_items", 10_000, "sample", "hashing-384", "2026-08-03T05:00:00Z"],
  ["users", 10_000, "exact", "hashing-384", "2026-08-31T05:40:00Z"],
  ["users", 10_000, "exact", "bge-small-en-v1.5", "2026-09-01T08:20:00Z"],
  ["orders", 10_000, "exact", "hashing-384", "2026-08-31T05:45:00Z"],
  ["orders", 10_000, "exact", "bge-small-en-v1.5", "2026-09-01T08:25:00Z"],
  ["order_items", 10_000, "exact", "hashing-384", "2026-08-31T05:50:00Z"],
  ["order_items", 10_000, "exact", "bge-small-en-v1.5", "2026-09-01T08:30:00Z"],
];

export function buildRagSets(samples: Record<string, { source: Row[] }>): RagSet[] {
  return SETS.map(([name, limit, tier, embedder, createdAt]) =>
    chunkSet(TABLES[name]!, samples[name]!.source, referenceDigest(name, limit, tier), embedder, createdAt),
  );
}

/** One `rag_chunks` row (the embedding as FLOAT64 REPEATED), for schema checks and the BigQuery-shaped API. */
export function chunkRow(set: RagSet, index: number): RagChunksRow {
  const chunk = set.chunks[index]!;
  return {
    chunk_id: chunk.chunk_id,
    source_fqn: chunk.source_fqn,
    source_pk: chunk.source_pk as RagChunksRow["source_pk"],
    row_digest: chunk.row_digest,
    reference_digest: chunk.reference_digest,
    chunk_index: chunk.chunk_index,
    chunk_kind: chunk.chunk_kind,
    chunk_text: chunk.chunk_text,
    embedder_id: chunk.embedder_id,
    embedder_version: chunk.embedder_version,
    embedding: Array.from(set.vectors.subarray(index * set.dim, (index + 1) * set.dim)),
    metadata: chunk.metadata as RagChunksRow["metadata"],
    created_at: chunk.created_at,
  };
}
