/**
 * RAG tab extras, served under `/api/x/rag` (registered by app.ts).
 *
 *   GET  /api/x/rag/projection?digest&embedder&space&ids&params
 *        → { hit: false } | { hit: true, n, coords, frame, trust, cached_at }
 *   POST /api/x/rag/projection   { digest, embedder, space, ids, params, n, coords, frame, trust }
 *
 * A UMAP of up to 3,000 × 384-d vectors takes seconds in the browser's worker;
 * this route keeps the finished 3-D layout in the server's in-memory LRU so
 * the next visit (or another tab on the same BFF) skips the fit. The key is
 * (reference digest, embedder, space, projection params) plus `ids`, a
 * SHA-256 over the chunk ids in plot order, so a layout is never applied to a
 * different set of points. Only derived coordinates are stored — no chunk
 * text, no vectors — in memory, never persisted. A miss answers 200
 * `{ hit: false }` (not 404) so the browser logs no error for an expected miss.
 */
import type { FastifyPluginAsync } from "fastify";
import { z } from "zod";

import { parse, type RouteOptions } from "./context";

const MAX_POINTS = 3000;

const keySchema = z.object({
  digest: z.string().regex(/^[0-9a-f]{16,128}$/),
  embedder: z.string().min(1).max(128),
  space: z.string().min(1).max(160),
  ids: z.string().regex(/^[0-9a-f]{64}$/),
  params: z.string().min(1).max(160),
});

const finite = z.number().refine(Number.isFinite, "must be finite");

const bodySchema = keySchema.extend({
  n: z.int().min(1).max(MAX_POINTS),
  coords: z.array(finite.refine((v) => Math.abs(v) <= 1e4, "out of range")).max(MAX_POINTS * 3),
  frame: z.object({ center: z.tuple([finite, finite, finite]), scale: finite }),
  trust: z.number().min(0).max(1).nullable(),
});

interface CachedProjection {
  n: number;
  coords: number[];
  frame: { center: [number, number, number]; scale: number };
  trust: number | null;
  cached_at: string;
}

function cacheKey(key: z.infer<typeof keySchema>): string {
  return `rag:projection\n${key.digest}\n${key.embedder}\n${key.space}\n${key.params}\n${key.ids}`;
}

const plugin: FastifyPluginAsync<RouteOptions> = (app, { cache }) => {
  app.get("/projection", (request) => {
    const key = parse(keySchema, request.query);
    const hit = cache.get(cacheKey(key)) as CachedProjection | undefined;
    return Promise.resolve(hit ? { hit: true, ...hit } : { hit: false });
  });

  app.post("/projection", (request, reply) => {
    const body = parse(bodySchema, request.body);
    if (body.coords.length !== body.n * 3) {
      return Promise.resolve(
        reply
          .code(400)
          .send({ statusCode: 400, error: "Bad Request", message: "coords must hold n × 3 numbers", details: [] }),
      );
    }
    const value: CachedProjection = {
      n: body.n,
      coords: body.coords,
      frame: body.frame,
      trust: body.trust,
      cached_at: new Date().toISOString(),
    };
    cache.set(cacheKey(body), value);
    return Promise.resolve(reply.code(201).send({ stored: true }));
  });
  return Promise.resolve();
};

export default plugin;
