/** `GET /api/trend?metric_id=…` — one metric across evaluations (with the evaluation filters). */
import { trendQuerySchema } from "@synthetic-platform/contracts";
import type { FastifyPluginAsync } from "fastify";

import { newContext } from "../providers/types";
import { parse, stamp, type RouteOptions } from "./context";

export const trendRoutes: FastifyPluginAsync<RouteOptions> = (app, { provider }) => {
  app.get("/api/trend", async (request, reply) => {
    const q = parse(trendQuerySchema, request.query);
    const ctx = newContext();
    const points = await provider.metricTrend(q, ctx);
    stamp(reply, provider, ctx);
    return points;
  });
  return Promise.resolve();
};
