/**
 * Which RAG set the page shows, resolved from the URL against the sets the
 * BFF lists (`/api/facets` → `rag`), with a fallback at every step so a stale
 * or hand-edited link never strands the reader: table → reference sample
 * (digest, newest first) → embedder → space.
 *
 * A space is the set of vectors the explorer and the simulator work on:
 *   rows          the `row_doc` vectors (the row index, ≤ 1,024)
 *   values:<col>  one column's `free_text_col` vectors (the column index —
 *                 the one that picks the seeds the LLM sees)
 *   all           every chunk of the set (row docs + values, ≤ 3,000)
 *   tables        the row docs of every table embedded with the same
 *                 embedder (colour by table; retrieval never crosses tables)
 */
import type { RagChunksQuery, RagSet } from "@contracts/api";

/** The API's cap per response (`RAG_CHUNKS_MAX`). */
export const CHUNKS_MAX = 3000;

export type SpaceId = "rows" | "all" | "tables" | `values:${string}`;

export function tableName(fqn: string): string {
  return fqn.split(".").pop() ?? fqn;
}

export function listTables(sets: readonly RagSet[]): string[] {
  return [...new Set(sets.map((s) => tableName(s.source_fqn)))];
}

export interface SampleOption {
  digest: string;
  createdAt: string | null;
  embedders: string[];
}

/** Reference samples (digests) of one table, newest first, with the embedders each was embedded with. */
export function samplesFor(sets: readonly RagSet[], table: string): SampleOption[] {
  const byDigest = new Map<string, SampleOption>();
  for (const s of sets) {
    if (tableName(s.source_fqn) !== table) continue;
    const option = byDigest.get(s.reference_digest) ?? {
      digest: s.reference_digest,
      createdAt: s.created_at,
      embedders: [],
    };
    if (!option.embedders.includes(s.embedder_id)) option.embedders.push(s.embedder_id);
    if (s.created_at && (!option.createdAt || s.created_at > option.createdAt)) option.createdAt = s.created_at;
    byDigest.set(s.reference_digest, option);
  }
  return [...byDigest.values()].sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""));
}

export interface RagSearchInput {
  table?: string;
  digest?: string;
  embedder?: string;
  space?: string;
}

export interface Resolved {
  table: string;
  samples: SampleOption[];
  set: RagSet;
  space: SpaceId;
  /** What was asked for and replaced, in words (shown once, politely). */
  notices: string[];
}

/** The embedder the query box can use in the browser. */
export const BROWSER_EMBEDDER = "hashing-384";

export function resolveSelection(sets: readonly RagSet[], search: RagSearchInput): Resolved | null {
  if (!sets.length) return null;
  const notices: string[] = [];
  const tables = listTables(sets);
  let table = search.table && tables.includes(search.table) ? search.table : undefined;
  if (search.table && !table) notices.push(`No RAG set for table "${search.table}"; showing ${tables[0]}.`);
  if (!table) {
    // Prefer a table with value chunks: it shows both indexes.
    table = tableName((sets.find((s) => s.value_chunks > 0) ?? sets[0]!).source_fqn);
  }
  const samples = samplesFor(sets, table);
  let sample = samples.find((s) => s.digest === search.digest);
  if (search.digest && !sample) notices.push("That reference sample has no RAG set; showing the newest.");
  sample ??= samples.find((s) => s.embedders.includes(search.embedder ?? BROWSER_EMBEDDER)) ?? samples[0]!;
  let embedder = search.embedder && sample.embedders.includes(search.embedder) ? search.embedder : undefined;
  if (search.embedder && !embedder)
    notices.push(`Embedder "${search.embedder}" has no vectors for this sample; showing ${sample.embedders[0]}.`);
  embedder ??= sample.embedders.includes(BROWSER_EMBEDDER) ? BROWSER_EMBEDDER : sample.embedders[0]!;
  const set =
    sets.find(
      (s) => tableName(s.source_fqn) === table && s.reference_digest === sample.digest && s.embedder_id === embedder,
    ) ?? sets[0]!;
  const space = resolveSpace(search.space, set, sets);
  if (search.space && search.space !== space)
    notices.push("That view is not available for this set; showing row documents.");
  return { table, samples, set, space, notices };
}

export function resolveSpace(space: string | undefined, set: RagSet, sets: readonly RagSet[]): SpaceId {
  if (!space || space === "rows") return "rows";
  if (space === "all") return set.value_chunks > 0 ? "all" : "rows";
  if (space === "tables") return tablesPeers(set, sets).length > 1 ? "tables" : "rows";
  if (space.startsWith("values:")) {
    const column = space.slice("values:".length);
    return set.columns.includes(column) ? `values:${column}` : "rows";
  }
  return "rows";
}

/** For the `tables` space: per table, the newest set with the same embedder id. */
export function tablesPeers(set: RagSet, sets: readonly RagSet[]): RagSet[] {
  const newest = new Map<string, RagSet>();
  for (const s of sets) {
    if (s.embedder_id !== set.embedder_id || s.row_docs === 0) continue;
    const t = tableName(s.source_fqn);
    // The selected set represents its own table.
    if (t === tableName(set.source_fqn)) {
      newest.set(t, set);
      continue;
    }
    const known = newest.get(t);
    if (!known || (s.created_at ?? "") > (known.created_at ?? "")) newest.set(t, s);
  }
  return [...newest.values()];
}

export function spaceColumn(space: SpaceId): string | null {
  return space.startsWith("values:") ? space.slice("values:".length) : null;
}

export function spaceLabel(space: SpaceId): string {
  if (space === "rows") return "Row documents";
  if (space === "all") return "Every chunk";
  if (space === "tables") return "Row documents of every table";
  return `Values of ${spaceColumn(space)}`;
}

/** Whether the pipeline ever retrieves over this space (seeds are hidden where it does not). */
export function spaceRetrieves(space: SpaceId): boolean {
  return space === "rows" || space.startsWith("values:");
}

export function embedderParam(set: RagSet): string {
  return `${set.embedder_id}/${set.embedder_version}`;
}

/** The chunk queries a space needs; every response stays under the API cap and the total under 3,000 vectors. */
export function spaceQueries(space: SpaceId, set: RagSet, sets: readonly RagSet[], rowLimit = 1024): RagChunksQuery[] {
  const base = { digest: set.reference_digest, embedder: embedderParam(set), source_fqn: set.source_fqn };
  const column = spaceColumn(space);
  if (column) return [{ ...base, kind: "free_text_col", column, limit: 1024 }];
  if (space === "all") {
    const rows = Math.min(set.row_docs, rowLimit);
    return [
      { ...base, kind: "row_doc", limit: Math.max(rows, 1) },
      { ...base, kind: "free_text_col", limit: Math.max(1, Math.min(set.value_chunks, CHUNKS_MAX - rows)) },
    ];
  }
  if (space === "tables") {
    const peers = tablesPeers(set, sets);
    const each = Math.floor(CHUNKS_MAX / Math.max(peers.length, 1));
    return peers.map((p) => ({
      digest: p.reference_digest,
      embedder: embedderParam(p),
      source_fqn: p.source_fqn,
      kind: "row_doc" as const,
      limit: Math.max(1, Math.min(each, rowLimit)),
    }));
  }
  return [{ ...base, kind: "row_doc", limit: Math.max(1, Math.min(set.row_docs || rowLimit, rowLimit)) }];
}
