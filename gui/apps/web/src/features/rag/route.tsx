/**
 * RAG route module (owned by the RAG tab agent). Contract: `searchSchema`
 * (`@/lib/search`, see intro/route.tsx) + lazy `component`; read params with
 * `getRouteApi("/rag").useSearch()`.
 *
 * Every param is optional and falls back to a sensible default in the page
 * (an unknown table, digest, embedder or space resolves to the newest set),
 * so a stale or hand-edited link never strands the reader.
 */
import { lazyRouteComponent } from "@tanstack/react-router";

import { integer, oneOf, searchParams, text, type SearchOf } from "@/lib/search";

/** The two embedders the pipeline ships; live data may name another (the page lists what the BFF has). */
export const embedders = ["hashing-384", "bge-small-en-v1.5"] as const;
export const strategyIds = ["centroid", "kcenter", "kcenter_rotate", "random", "mmr"] as const;
export const projections = ["pca", "umap"] as const;
export const views = ["3d", "2d"] as const;
export const colorBys = ["kind", "column", "table", "cluster"] as const;

const optionalString = text({ max: 200 });

export const searchSchema = searchParams({
  /** Source table (short name, e.g. "users"). */
  table: optionalString,
  /** Reference digest (sha256 hex). */
  digest: optionalString,
  /** embedder_id. */
  embedder: optionalString,
  /** "rows" | "values:<column>" | "all" | "tables". */
  space: optionalString,
  strategy: oneOf(strategyIds),
  /** Seeds per prompt (the pipeline uses 8). */
  k: integer({ min: 1, max: 16 }),
  /** kcenter_rotate: the ladder attempt. */
  attempt: integer({ min: 0, max: 7 }),
  proj: oneOf(projections),
  view: oneOf(views),
  color: oneOf(colorBys),
  /** Free-text pools: the LLM weights URI. */
  model: optionalString,
});
export type RagSearch = SearchOf<typeof searchSchema>;

export const component = lazyRouteComponent(() => import("./RagPage"), "RagPage");
