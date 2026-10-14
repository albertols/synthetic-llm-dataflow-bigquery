/** `GET /api/compare?ids=a,b,…` — aligned metric cells, parameter diffs and comparability flags. */
import { compareQuerySchema } from "@synthetic-platform/contracts";
import type { FastifyPluginAsync } from "fastify";

import { newContext } from "../providers/types";
import { parse, stamp, type RouteOptions } from "./context";

export const compareRoutes: FastifyPluginAsync<RouteOptions> = (app, { provider }) => {
  app.get("/api/compare", async (request, reply) => {
    const { ids } = parse(compareQuerySchema, request.query);
    const ctx = newContext();
    const comparison = await provider.compare([...new Set(ids)], ctx);
    stamp(reply, provider, ctx);
    return comparison;
  });
  return Promise.resolve();
};
