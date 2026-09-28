/**
 * `GET /api/relationships` — the relationship models a registry row can name: the
 * committed sample models (generated/relationships.json) plus, in mock mode, the
 * mock's invented model. Real models (gitignored, or on GCS) are never read here.
 */
import { relationships, type RelationshipsResponse } from "@synthetic-platform/contracts";
import type { FastifyPluginAsync } from "fastify";

import type { RouteOptions } from "./context";

export const relationshipsRoutes: FastifyPluginAsync<RouteOptions> = (app, { provider }) => {
  app.get("/api/relationships", async (): Promise<RelationshipsResponse> => {
    const extra = await provider.relationshipModels();
    return {
      models: [
        ...relationships.models.map((m) => ({ ...m, origin: "committed_example" as const })),
        ...extra.map((m) => ({ ...m, origin: "mock" as const })),
      ],
    };
  });
  return Promise.resolve();
};
