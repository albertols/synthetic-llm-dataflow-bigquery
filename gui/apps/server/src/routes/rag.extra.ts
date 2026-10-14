/**
 * RAG tab extras, served under `/api/x/rag` (registered by app.ts).
 *
 *   GET  /api/x/rag/projection?digest&embedder&space&ids&params
 *        → { hit: false } | { hit: true, n, coords, frame, trust, cached_at }
 *   POST /api/x/rag/projection   { digest, embedder, space, ids, params, n, coords, frame, trust } → 201
 *
 * A UMAP of up to 3,000 × 384-d vectors takes seconds in the browser's worker;
 * this route keeps the finished 3-D layout in the server's in-memory LRU so
 * the next visit (or another tab on the same BFF) skips the fit. The key is
 * (reference digest, embedder, space, projection params) plus `ids`, a
 * SHA-256 over the chunk ids in plot order, so a layout is never applied to a
 * different set of points. Only derived coordinates are stored — no chunk
 * text, no vectors — in memory, never persisted. A miss answers 200
 * `{ hit: false }` (not 404) so the browser logs no error for an expected miss.
 *
 * `coords` travels as base64 of n × 3 little-endian Float32 (the same bytes
 * the worker produced): 3,000 points are 36,000 bytes, 48,000 base64
 * characters, inside the BFF's 64 KB body limit — a JSON number array of the
 * same layout (~81 KB) is not. Decoding checks the exact byte length
 * (n × 3 × 4) and that every value is finite and within ±1e4.
 */
import type { FastifyPluginAsync } from "fastify";
import { z } from "zod";

import { parse, type RouteOptions } from "./context";

const MAX_POINTS = 3000;
const BYTES_PER_POINT = 3 * 4;
const MAX_COORD = 1e4;

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
  /** base64 of n × 3 Float32 LE; the length is checked against n after decoding. */
  coords: z
    .string()
    .max(Math.ceil((MAX_POINTS * BYTES_PER_POINT) / 3) * 4)
    .regex(/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/, "must be base64"),
  frame: z.object({ center: z.tuple([finite, finite, finite]), scale: finite }),
  trust: z.number().min(0).max(1).nullable(),
});

interface CachedProjection {
  n: number;
  /** base64 of n × 3 Float32 LE, as posted (validated). */
  coords: string;
  frame: { center: [number, number, number]; scale: number };
  trust: number | null;
  cached_at: string;
}

/** Decode and check a layout: exactly n × 3 finite Float32 values within ±1e4, else the reason. */
export function checkCoords(base64: string, n: number): string | null {
  const bytes = Buffer.from(base64, "base64");
  if (bytes.length !== n * BYTES_PER_POINT)
    return `coords must hold n × 3 Float32 values (${n * BYTES_PER_POINT} bytes), got ${bytes.length} bytes`;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let offset = 0; offset < bytes.length; offset += 4) {
    const v = view.getFloat32(offset, true);
    if (!Number.isFinite(v) || Math.abs(v) > MAX_COORD)
      return `coords[${offset / 4}] is not a finite value within ±1e4`;
  }
  return null;
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
    const problem = checkCoords(body.coords, body.n);
    if (problem)
      return Promise.resolve(
        reply.code(400).send({ statusCode: 400, error: "Bad Request", message: problem, details: [] }),
      );
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
