/** `GET /api/runs` (validation_runs, newest first) and `GET /api/dlq?run_ids=…` (DLQ grouped by rule). */
import { dlqQuerySchema, runFilterSchema } from "@synthetic-platform/contracts";
import type { FastifyPluginAsync } from "fastify";

import { newContext } from "../providers/types";
import { parse, stamp, type RouteOptions } from "./context";

export const runsRoutes: FastifyPluginAsync<RouteOptions> = (app, { provider }) => {
  app.get("/api/runs", async (request, reply) => {
    const q = parse(runFilterSchema, request.query);
    const ctx = newContext();
    const runs = await provider.runs(q, ctx);
    stamp(reply, provider, ctx);
    return runs;
  });
  app.get("/api/dlq", async (request, reply) => {
    const { run_ids } = parse(dlqQuerySchema, request.query);
    const ctx = newContext();
    const summary = await provider.dlqSummary(run_ids, ctx);
    stamp(reply, provider, ctx);
    return summary;
  });
  return Promise.resolve();
};
