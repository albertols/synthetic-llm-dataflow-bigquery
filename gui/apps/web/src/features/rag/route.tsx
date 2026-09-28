/**
 * RAG route module (owned by the RAG tab agent). Contract: `searchSchema`
 * (`zod/mini`, see intro/route.tsx) + lazy `component`; read params with
 * `getRouteApi("/rag").useSearch()`.
 */
import { lazyRouteComponent } from "@tanstack/react-router";
import { z } from "zod/mini";

export const embedders = ["hashing-384", "bge-small-en-v1.5"] as const;

export const searchSchema = z.object({
  table: z.catch(z.optional(z.string()), undefined),
  digest: z.catch(z.optional(z.string()), undefined),
  embedder: z.catch(z.optional(z.enum(embedders)), undefined),
});
export type RagSearch = z.infer<typeof searchSchema>;

export const component = lazyRouteComponent(() => import("./RagPage"), "RagPage");
