/**
 * Server configuration from the environment, validated with zod at startup.
 *
 *   DATA_SOURCE        mock (default) | bigquery
 *   GCP_PROJECT        required in bigquery mode (the runner's ADC project)
 *   BQ_LOCATION        BigQuery location of the datasets (default europe-west3, ADR 0004)
 *   QUALITY_DATASET    synthetic_data_quality
 *   RAG_DATASET        synthetic_rag
 *   MAX_BYTES_BILLED   bytes cap on every query (default 10 GiB)
 *   HOST               127.0.0.1 — the BFF never binds a public interface by default
 *   PORT               falls back to GUI_SERVER_PORT, then 8787
 *   GUI_WEB_PORT       the Vite dev server (5173), whose proxy forwards its own Host
 *   ALLOWED_HOSTS      extra `host:port` values the Host guard accepts (comma-separated),
 *                      e.g. a container published on another port
 *   STATIC_DIR         the built SPA (default apps/web/dist)
 *   CACHE_MAX_ENTRIES / CACHE_TTL_SECONDS   the in-memory LRU of query results (bigquery mode)
 */
import { fileURLToPath } from "node:url";

import { z } from "zod";

const GIB = 1024 ** 3;
const identifier = z.string().regex(/^[A-Za-z_][A-Za-z0-9_]*$/, "a BigQuery dataset name (letters, digits, _)");

const port = z.coerce.number().int().min(1).max(65_535);

export const configSchema = z
  .object({
    DATA_SOURCE: z.enum(["mock", "bigquery"]).default("mock"),
    GCP_PROJECT: z
      .string()
      .regex(/^[a-z][a-z0-9-]{4,61}[a-z0-9]$/, "a GCP project id")
      .optional(),
    BQ_LOCATION: z.string().min(2).default("europe-west3"),
    QUALITY_DATASET: identifier.default("synthetic_data_quality"),
    RAG_DATASET: identifier.default("synthetic_rag"),
    MAX_BYTES_BILLED: z.coerce
      .number()
      .int()
      .positive()
      .default(10 * GIB),
    HOST: z.string().min(1).default("127.0.0.1"),
    PORT: port.optional(),
    GUI_SERVER_PORT: port.optional(),
    GUI_WEB_PORT: port.default(5173),
    ALLOWED_HOSTS: z
      .string()
      .default("")
      .transform((v) =>
        v
          .split(",")
          .map((h) => h.trim().toLowerCase())
          .filter(Boolean),
      )
      .pipe(z.array(z.string().regex(/^[a-z0-9.\-[\]:]+:\d{1,5}$/, "host:port"))),
    STATIC_DIR: z.string().default(fileURLToPath(new URL("../../web/dist", import.meta.url))),
    CACHE_MAX_ENTRIES: z.coerce.number().int().positive().default(500),
    CACHE_TTL_SECONDS: z.coerce.number().int().positive().default(300),
    LOG_LEVEL: z.enum(["fatal", "error", "warn", "info", "debug", "trace", "silent"]).default("info"),
  })
  .superRefine((env, ctx) => {
    if (env.DATA_SOURCE === "bigquery" && !env.GCP_PROJECT)
      ctx.addIssue({
        code: "custom",
        path: ["GCP_PROJECT"],
        message: "GCP_PROJECT is required when DATA_SOURCE=bigquery",
      });
  })
  .transform(({ PORT, GUI_SERVER_PORT, ...rest }) => ({ ...rest, PORT: PORT ?? GUI_SERVER_PORT ?? 8787 }));

export type ServerConfig = z.output<typeof configSchema>;

/** Parses `env` (default: process.env); throws a readable error listing every invalid variable. */
export function loadConfig(env: Record<string, string | undefined> = process.env): ServerConfig {
  const parsed = configSchema.safeParse(env);
  if (!parsed.success) {
    const lines = parsed.error.issues.map((i) => `  ${i.path.join(".") || "(env)"}: ${i.message}`);
    throw new Error(`Invalid server configuration:\n${lines.join("\n")}`);
  }
  return parsed.data;
}

const LOOPBACK = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

/** HOST binds only the loopback interface. */
export function isLoopbackHost(host: string): boolean {
  return LOOPBACK.has(host.toLowerCase()) || /^127(\.\d{1,3}){3}$/.test(host);
}

/**
 * The `Host` values the BFF answers (DNS-rebinding guard): the loopback names on PORT
 * and on the Vite dev port (its proxy forwards the browser's Host), HOST itself when it
 * names a specific interface, and ALLOWED_HOSTS.
 */
export function allowedHosts(config: ServerConfig): Set<string> {
  const hosts = new Set<string>();
  for (const port of [config.PORT, config.GUI_WEB_PORT])
    for (const name of ["127.0.0.1", "localhost", "[::1]"]) hosts.add(`${name}:${port}`);
  if (!["0.0.0.0", "::", "[::]"].includes(config.HOST) && !isLoopbackHost(config.HOST))
    hosts.add(`${config.HOST.toLowerCase()}:${config.PORT}`);
  for (const host of config.ALLOWED_HOSTS) hosts.add(host);
  return hosts;
}
