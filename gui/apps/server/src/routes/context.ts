/**
 * What every route plugin receives, and the helpers they share: parse the
 * query with the contract's zod (400 on failure), and stamp the BigQuery
 * accounting headers on the way out.
 */
import { HEADER_BYTES_ESTIMATE } from "@synthetic-platform/contracts";
import type { FastifyReply } from "fastify";
import type { LRUCache } from "lru-cache";
import type { z } from "zod";

import type { ServerConfig } from "../config";
import type { DataProvider, QueryContext } from "../providers/types";

export interface RouteOptions {
  provider: DataProvider;
  config: ServerConfig;
  /** A small in-memory cache routes may use for derived results (never persisted). */
  cache: LRUCache<string, object>;
}

export class BadRequest extends Error {
  constructor(readonly issues: string[]) {
    super(`invalid request: ${issues.join("; ")}`);
    this.name = "BadRequest";
  }
}

export function parse<S extends z.ZodType>(schema: S, value: unknown): z.output<S> {
  const result = schema.safeParse(value);
  if (!result.success)
    throw new BadRequest(result.error.issues.map((i) => `${i.path.join(".") || "query"}: ${i.message}`));
  return result.data;
}

/** `x-bq-bytes-estimate` (bigquery mode), plus how many named queries ran and how many were cache hits. */
export function stamp(reply: FastifyReply, provider: DataProvider, ctx: QueryContext) {
  if (provider.mode !== "bigquery") return;
  reply.header(HEADER_BYTES_ESTIMATE, String(ctx.bytesEstimate));
  reply.header("x-bq-queries", String(ctx.queries));
  reply.header("x-bq-cache-hits", String(ctx.cacheHits));
}
