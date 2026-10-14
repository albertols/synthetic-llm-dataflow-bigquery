/** `GET /api/evaluations` (the filtered list) and `GET /api/facets` (filter options and headline counts). */
import { evaluationFilterSchema } from "@synthetic-platform/contracts";
import type { FastifyPluginAsync } from "fastify";

import { newContext } from "../providers/types";
import { parse, stamp, type RouteOptions } from "./context";

export const evaluationsRoutes: FastifyPluginAsync<RouteOptions> = (app, { provider }) => {
  app.get("/api/evaluations", async (request, reply) => {
    const q = parse(evaluationFilterSchema, request.query);
    const ctx = newContext();
    const page = await provider.listEvaluations(q, ctx);
    stamp(reply, provider, ctx);
    return page;
  });
  app.get("/api/facets", async (_request, reply) => {
    const ctx = newContext();
    const facets = await provider.facets(ctx);
    stamp(reply, provider, ctx);
    return facets;
  });
  return Promise.resolve();
};
