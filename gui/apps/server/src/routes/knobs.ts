/** `GET /api/knobs` (generated knobs.json: values from code with path:line) and `GET /api/catalogue` (metrics.yaml). */
import { catalogue, catalogueFamilies, catalogueLevels, catalogueVersion, knobs } from "@synthetic-platform/contracts";
import type { FastifyPluginAsync } from "fastify";

import type { RouteOptions } from "./context";

export const knobsRoutes: FastifyPluginAsync<RouteOptions> = (app) => {
  app.get("/api/knobs", () => knobs);
  app.get("/api/catalogue", () => ({
    version: catalogueVersion,
    levels: catalogueLevels,
    families: catalogueFamilies,
    metrics: catalogue,
  }));
  return Promise.resolve();
};
