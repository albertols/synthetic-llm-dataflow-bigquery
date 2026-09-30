/**
 * `GET /api/source-stats?table=…[&tier=sample|exact][&digest=…][&snapshot=key,key]` — the
 * profiler's rows for one source table, grouped into snapshots keyed by (digest, tier,
 * profiler_version, run_id); NULL stats_tier reads as sample.
 */
import { sourceStatsQuerySchema } from "@synthetic-platform/contracts";
import type { FastifyPluginAsync } from "fastify";

import { newContext } from "../providers/types";
import { parse, stamp, type RouteOptions } from "./context";

export const sourceStatsRoutes: FastifyPluginAsync<RouteOptions> = (app, { provider }) => {
  app.get("/api/source-stats", async (request, reply) => {
    const q = parse(sourceStatsQuerySchema, request.query);
    const ctx = newContext();
    const stats = await provider.sourceStats(q, ctx);
    stamp(reply, provider, ctx);
    if (!stats)
      return reply
        .code(404)
        .send({ statusCode: 404, error: "Not Found", message: `no source_table_stats for "${q.table}"` });
    return stats;
  });
  return Promise.resolve();
};
