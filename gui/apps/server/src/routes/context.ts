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

/** JSON text with every character outside printable ASCII escaped (`\uXXXX`): safe in a header, still valid JSON. */
function asciiJson(value: unknown): string {
  return JSON.stringify(value).replace(/[^\x20-\x7e]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

const WARNINGS_SHOWN = 5;
const WARNING_MAX_CHARS = 200;
const WARNINGS_HEADER_MAX = 1000;

/**
 * `x-contract-warnings`: a JSON array of findings (ASCII-escaped, at most five, each at
 * most 200 characters, the whole header at most 1,000), then "… and N more" when some
 * were left out. JSON, not "; "-joined text: a finding may itself contain "; ".
 */
export function contractWarningsHeader(warnings: readonly string[]): string {
  const clip = (text: string) => (text.length > WARNING_MAX_CHARS ? `${text.slice(0, WARNING_MAX_CHARS - 1)}…` : text);
  let shown = Math.min(warnings.length, WARNINGS_SHOWN);
  for (;;) {
    const list = warnings.slice(0, shown).map(clip);
    if (shown < warnings.length) list.push(`… and ${warnings.length - shown} more`);
    const header = asciiJson(list);
    if (header.length <= WARNINGS_HEADER_MAX || shown === 0) return header;
    shown -= 1;
  }
}

/**
 * `x-bq-bytes-estimate` (bigquery mode), how many named queries ran and how many were
 * cache hits, and `x-contract-warnings` when live rows carried vocabulary values the
 * contract does not know yet (a JSON array, `contractWarningsHeader`).
 */
export function stamp(reply: FastifyReply, provider: DataProvider, ctx: QueryContext) {
  if (ctx.warnings.length) reply.header(HEADER_CONTRACT_WARNINGS, contractWarningsHeader(ctx.warnings));
  if (provider.mode !== "bigquery") return;
  reply.header(HEADER_BYTES_ESTIMATE, String(ctx.bytesEstimate));
  reply.header("x-bq-queries", String(ctx.queries));
  reply.header("x-bq-cache-hits", String(ctx.cacheHits));
}
