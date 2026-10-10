/** `GET /api/health`: the data source, datasets, bytes cap and contract build. Never touches BigQuery. */
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

import { catalogueVersion, type Health } from "@synthetic-platform/contracts";
import type { FastifyPluginAsync } from "fastify";

import type { RouteOptions } from "./context";

const MANIFEST = new URL("../../../../packages/contracts/generated/manifest.json", import.meta.url);
const PACKAGE = new URL("../../package.json", import.meta.url);

export const healthRoutes: FastifyPluginAsync<RouteOptions> = (app, { provider, config }) => {
  const startedAt = new Date().toISOString();
  const contractsDigest = createHash("sha256").update(readFileSync(MANIFEST)).digest("hex").slice(0, 16);
  const serverVersion = (JSON.parse(readFileSync(PACKAGE, "utf8")) as { version: string }).version;
  app.get("/api/health", (): Health => ({
    status: "ok",
    mode: provider.mode,
    project: provider.project,
    location: provider.location,
    datasets: { quality: config.QUALITY_DATASET, rag: config.RAG_DATASET },
    max_bytes_billed: config.MAX_BYTES_BILLED,
    catalogue_version: catalogueVersion,
    contracts_digest: contractsDigest,
    server_version: serverVersion,
    started_at: startedAt,
  }));
  return Promise.resolve();
};
