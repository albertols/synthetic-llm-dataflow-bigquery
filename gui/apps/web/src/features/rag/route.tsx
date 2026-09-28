/**
 * RAG route module (owned by the RAG tab agent). Contract: `searchSchema`
 * (`zod/mini`, see intro/route.tsx) + lazy `component`; read params with
 * `getRouteApi("/rag").useSearch()`.
 *
 * Every param is optional and falls back to a sensible default in the page
 * (an unknown table, digest, embedder or space resolves to the newest set),
 * so a stale or hand-edited link never strands the reader.
 */
import { lazyRouteComponent } from "@tanstack/react-router";
import { z } from "zod/mini";

/** The two embedders the pipeline ships; live data may name another (the page lists what the BFF has). */
export const embedders = ["hashing-384", "bge-small-en-v1.5"] as const;
export const strategyIds = ["centroid", "kcenter", "kcenter_rotate", "random", "mmr"] as const;
export const projections = ["pca", "umap"] as const;
export const views = ["3d", "2d"] as const;
export const colorBys = ["kind", "column", "table", "cluster"] as const;

const optionalString = z.catch(z.optional(z.string().check(z.maxLength(200))), undefined);

export const searchSchema = z.object({
  /** Source table (short name, e.g. "users"). */
  table: optionalString,
  /** Reference digest (sha256 hex). */
  digest: optionalString,
  /** embedder_id. */
  embedder: optionalString,
  /** "rows" | "values:<column>" | "all" | "tables". */
  space: optionalString,
  strategy: z.catch(z.optional(z.enum(strategyIds)), undefined),
  /** Seeds per prompt (the pipeline uses 8). */
  k: z.catch(z.optional(z.int().check(z.minimum(1), z.maximum(16))), undefined),
  /** kcenter_rotate: the ladder attempt. */
  attempt: z.catch(z.optional(z.int().check(z.minimum(0), z.maximum(7))), undefined),
  proj: z.catch(z.optional(z.enum(projections)), undefined),
  view: z.catch(z.optional(z.enum(views)), undefined),
  color: z.catch(z.optional(z.enum(colorBys)), undefined),
  /** Free-text pools: the LLM weights URI. */
  model: optionalString,
});
export type RagSearch = z.infer<typeof searchSchema>;

export const component = lazyRouteComponent(() => import("./RagPage"), "RagPage");
