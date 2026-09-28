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
