/**
 * `GET /api/evaluations/:id` — the registry row (latest event), every event, metrics
 * and flags; profiles only with `?profiles=all` (the default is `none`: a detail page
 * loads them per drawer from `/profiles`, never hundreds of payloads up front); `GET /api/evaluations/:id/profiles`
 * — profiles of one table / column / kind / side, for lazy drawers.
 */
import { evaluationDetailQuerySchema, profileQuerySchema } from "@synthetic-platform/contracts";
import type { FastifyPluginAsync } from "fastify";
import { z } from "zod";

import { newContext } from "../providers/types";
import { parse, stamp, type RouteOptions } from "./context";

const idParams = z.object({ id: z.string().min(1).max(200) });

export const evaluationRoutes: FastifyPluginAsync<RouteOptions> = (app, { provider }) => {
  app.get("/api/evaluations/:id", async (request, reply) => {
    const { id } = parse(idParams, request.params);
    const q = parse(evaluationDetailQuerySchema, request.query);
    const ctx = newContext();
    const detail = await provider.getEvaluation(id, { profiles: q.profiles }, ctx);
    stamp(reply, provider, ctx);
    if (!detail) return reply.code(404).send({ statusCode: 404, error: "Not Found", message: `no evaluation "${id}"` });
    return detail;
  });
  app.get("/api/evaluations/:id/profiles", async (request, reply) => {
    const { id } = parse(idParams, request.params);
    const q = parse(profileQuerySchema, request.query);
    const ctx = newContext();
    const rows = await provider.profiles(id, q, ctx);
    stamp(reply, provider, ctx);
    if (!rows) return reply.code(404).send({ statusCode: 404, error: "Not Found", message: `no evaluation "${id}"` });
    return rows;
  });
  return Promise.resolve();
};
