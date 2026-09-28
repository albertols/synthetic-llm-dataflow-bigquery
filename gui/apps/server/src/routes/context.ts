/**
 * What every route plugin receives, and the helpers they share: parse the
 * query with the contract's zod (400 on failure), and stamp the BigQuery
 * accounting headers on the way out.
 */
import { HEADER_BYTES_ESTIMATE, HEADER_CONTRACT_WARNINGS } from "@synthetic-platform/contracts";
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

/** Header-safe text: printable ASCII only, bounded. */
function headerText(text: string, max: number): string {
  const ascii = text.replace(/[^\x20-\x7e]/g, "?");
  return ascii.length > max ? `${ascii.slice(0, max - 3)}...` : ascii;
}

/**
 * `x-bq-bytes-estimate` (bigquery mode), how many named queries ran and how many were
 * cache hits, and `x-contract-warnings` when live rows carried vocabulary values the
 * contract does not know yet ("<n>; <first findings>").
 */
export function stamp(reply: FastifyReply, provider: DataProvider, ctx: QueryContext) {
  if (ctx.warnings.length)
    reply.header(
      HEADER_CONTRACT_WARNINGS,
      headerText(`${ctx.warnings.length}; ${ctx.warnings.slice(0, 5).join("; ")}`, 1000),
    );
  if (provider.mode !== "bigquery") return;
  reply.header(HEADER_BYTES_ESTIMATE, String(ctx.bytesEstimate));
  reply.header("x-bq-queries", String(ctx.queries));
  reply.header("x-bq-cache-hits", String(ctx.cacheHits));
}
