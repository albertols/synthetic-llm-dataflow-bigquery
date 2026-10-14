/**
 * `GET /api/rag/chunks` — chunk metadata + Float32 embeddings in one binary
 * envelope (contracts/src/vectors.ts; `application/octet-stream`, ≤ 3,000 chunks =
 * RAG_CHUNKS_MAX: 3,000 × 384-d Float32 is 4.6 MB, inside the 5 MB vector budget; no
 * source_pk, see ChunkMeta);
 * `GET /api/rag/pools?digest=…` — the free-text pools of a reference digest.
 */
import {
  encodeVectorEnvelope,
  freetextPoolsQuerySchema,
  HEADER_VECTOR_COUNT,
  HEADER_VECTOR_DIM,
  ragChunksQuerySchema,
} from "@synthetic-platform/contracts";
import type { FastifyPluginAsync } from "fastify";

import { newContext } from "../providers/types";
import { parse, stamp, type RouteOptions } from "./context";

export const ragRoutes: FastifyPluginAsync<RouteOptions> = (app, { provider }) => {
  app.get("/api/rag/chunks", async (request, reply) => {
    const q = parse(ragChunksQuerySchema, request.query);
    const ctx = newContext();
    const { meta, dim, vectors } = await provider.ragChunks(q, ctx);
    stamp(reply, provider, ctx);
    const body = encodeVectorEnvelope(meta, vectors, dim);
    return reply
      .header("content-type", "application/octet-stream")
      .header(HEADER_VECTOR_DIM, String(dim))
      .header(HEADER_VECTOR_COUNT, String(meta.length))
      .send(Buffer.from(body.buffer, body.byteOffset, body.byteLength));
  });
  app.get("/api/rag/pools", async (request, reply) => {
    const q = parse(freetextPoolsQuerySchema, request.query);
    const ctx = newContext();
    const pools = await provider.freetextPools({ digest: q.digest, modelUri: q.model_uri, column: q.column }, ctx);
    stamp(reply, provider, ctx);
    return pools;
  });
  return Promise.resolve();
};
