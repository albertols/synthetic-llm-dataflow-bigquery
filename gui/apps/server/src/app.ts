/**
 * The Synthetic Platform BFF (ADR 0042): a Fastify app serving `/api/*` from a
 * DataProvider (mock or BigQuery, identical routes) and the built SPA.
 *
 * Routes: /api/health, /api/facets, /api/evaluations, /api/evaluations/:id,
 * /api/evaluations/:id/profiles, /api/compare, /api/trend, /api/runs, /api/dlq,
 * /api/source-stats, /api/rag/chunks (binary), /api/rag/pools, /api/relationships,
 * /api/knobs, /api/catalogue. A tab may add read-only routes in `routes/<tab>.extra.ts`
 * (evaluation, rag, config): a default-exported Fastify plugin, registered
 * under `/api/x/<tab>` with the same RouteOptions.
 *
 * Request guard (onRequest, before any route): the `Host` header must be one of
 * `allowedHosts(config)` — a DNS-rebinding page reaches 127.0.0.1 under its own
 * name and is refused — and on `/api/*` a cross-site browser request
 * (`Sec-Fetch-Site: cross-site`, or an `Origin` that is not an allowed host) is
 * refused, so a foreign page can never make the BFF spend BigQuery bytes.
 */
import { existsSync } from "node:fs";
import { join } from "node:path";

import fastifyStatic from "@fastify/static";
import { HEADER_DATA_SOURCE } from "@synthetic-platform/contracts";
import Fastify, { type FastifyInstance, type FastifyPluginAsync } from "fastify";
import { LRUCache } from "lru-cache";

import { allowedHosts, type ServerConfig } from "./config";
import { BytesCapError, ContractError, type DataProvider } from "./providers/types";
import { compareRoutes } from "./routes/compare";
import { BadRequest, type RouteOptions } from "./routes/context";
import { evaluationRoutes } from "./routes/evaluation";
import { evaluationsRoutes } from "./routes/evaluations";
import { healthRoutes } from "./routes/health";
import { knobsRoutes } from "./routes/knobs";
import { ragRoutes } from "./routes/rag";
import { relationshipsRoutes } from "./routes/relationships";
import { runsRoutes } from "./routes/runs";
import { sourceStatsRoutes } from "./routes/sourceStats";
import { trendRoutes } from "./routes/trend";

export const EXTRA_ROUTE_TABS = ["evaluation", "rag", "config"] as const;

export interface AppOptions {
  config: ServerConfig;
  provider: DataProvider;
  /** Serve apps/web/dist (default true). */
  serveStatic?: boolean;
}

export async function buildApp({ config, provider, serveStatic = true }: AppOptions): Promise<FastifyInstance> {
  const app = Fastify({
    logger: config.LOG_LEVEL === "silent" ? false : { level: config.LOG_LEVEL },
    bodyLimit: 64 * 1024,
  });

  const hosts = allowedHosts(config);
  const origins = new Set([...hosts].map((h) => `http://${h}`));
  const forbidden = (message: string) => ({ statusCode: 403, error: "Forbidden", message });
  app.addHook("onRequest", (request, reply, done) => {
    const host = (request.headers.host ?? "").toLowerCase();
    if (!hosts.has(host)) {
      request.log.warn({ host }, "refused: Host is not an allowed name");
      void reply.code(403).send(forbidden(`Host "${host}" is not served here (see ALLOWED_HOSTS)`));
      return;
    }
    if (request.url.startsWith("/api/")) {
      const site = request.headers["sec-fetch-site"];
      const origin = request.headers.origin;
      if (site === "cross-site" || (origin !== undefined && !origins.has(origin.toLowerCase()))) {
        request.log.warn({ site, origin }, "refused: cross-site API request");
        void reply.code(403).send(forbidden("cross-site requests to /api are refused"));
        return;
      }
    }
    done();
  });

  app.addHook("onSend", (request, reply, payload, done) => {
    reply.header("x-content-type-options", "nosniff");
    reply.header("referrer-policy", "no-referrer");
    reply.header("x-frame-options", "DENY");
    if (request.url.startsWith("/api/")) {
      reply.header(HEADER_DATA_SOURCE, provider.mode);
      reply.header("cache-control", "no-store");
    }
    done(null, payload);
  });

  app.setErrorHandler((error, request, reply) => {
    if (error instanceof BadRequest)
      return reply
        .code(400)
        .send({ statusCode: 400, error: "Bad Request", message: error.message, details: error.issues });
    if (error instanceof ContractError) {
      request.log.error({ query: error.query, details: error.details }, "contract mismatch");
      return reply
        .code(502)
        .send({ statusCode: 502, error: "Contract Mismatch", message: error.message, details: error.details });
    }
    if (error instanceof BytesCapError)
      return reply.code(422).send({
        statusCode: 422,
        error: "Bytes Cap",
        message: error.message,
        details: [`estimate=${error.estimate}`, `cap=${error.cap}`],
      });
    const status =
      typeof (error as { statusCode?: unknown }).statusCode === "number"
        ? (error as { statusCode: number }).statusCode
        : 500;
    if (status >= 500) request.log.error(error);
    return reply.code(status).send({
      statusCode: status,
      error: status >= 500 ? "Internal Server Error" : "Error",
      message: status >= 500 ? "the server could not answer this request" : (error as Error).message,
    });
  });

  const options: RouteOptions = {
    provider,
    config,
    cache: new LRUCache<string, object>({ max: 64, ttl: 15 * 60_000 }),
  };
  for (const plugin of [
    healthRoutes,
    evaluationsRoutes,
    evaluationRoutes,
    compareRoutes,
    trendRoutes,
    runsRoutes,
    sourceStatsRoutes,
    ragRoutes,
    relationshipsRoutes,
    knobsRoutes,
  ])
    await app.register(plugin, options);

  for (const tab of EXTRA_ROUTE_TABS) {
    const url = new URL(`./routes/${tab}.extra.ts`, import.meta.url);
    if (!existsSync(url)) continue;
    const mod = (await import(url.href)) as { default?: FastifyPluginAsync<RouteOptions> };
    if (mod.default) await app.register(mod.default, { ...options, prefix: `/api/x/${tab}` });
  }

  const index = join(config.STATIC_DIR, "index.html");
  const hasSpa = serveStatic && existsSync(index);
  if (hasSpa) await app.register(fastifyStatic, { root: config.STATIC_DIR, wildcard: false, index: "index.html" });
  app.setNotFoundHandler((request, reply) => {
    if (request.url.startsWith("/api/") || request.method !== "GET")
      return reply.code(404).send({
        statusCode: 404,
        error: "Not Found",
        message: `no route ${request.method} ${request.url.split("?")[0]}`,
      });
    if (!hasSpa)
      return reply
        .code(503)
        .type("text/plain")
        .send("The web app is not built: run `npm run build` in gui/ (or use `npm run dev`).");
    // SPA fallback: client-side routes (/evaluation/…, /rag, …) load index.html.
    return reply.type("text/html").sendFile("index.html");
  });
  return app;
}
