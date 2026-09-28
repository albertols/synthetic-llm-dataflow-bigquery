/**
 * The BigQuery provider against a stubbed client that returns rows in the
 * shapes `@google-cloud/bigquery` produces (BigQueryTimestamp objects, INT64
 * as strings, JSON as text). Asserts the safety contract — parameters only,
 * a dry run before every job, maximumBytesBilled on both, the cap enforced,
 * read-only SQL — and that normalized live rows validate against the same zod
 * as the mock (or fail loudly as a ContractError).
 */
import {
  bqTables,
  evaluationDetailSchema,
  facetsSchema,
  type BqField,
  type BqTableName,
} from "@synthetic-platform/contracts";
import { getMockDataset } from "@synthetic-platform/mock";
import { describe, expect, it } from "vitest";

import { buildApp } from "../app";
import { loadConfig } from "../config";
import { assertReadOnly, QUERIES } from "../queries/registry";
import { BigQueryProvider, type BigQueryClient } from "./bigquery";
import { BytesCapError, ContractError, newContext } from "./types";

const config = loadConfig({
  DATA_SOURCE: "bigquery",
  GCP_PROJECT: "demo-project",
  MAX_BYTES_BILLED: "1000000000",
  LOG_LEVEL: "silent",
});

/** A value as the Node client returns it: TIMESTAMP objects, INT64 text, JSON text. */
function recorded(value: unknown, field: BqField): unknown {
  if (value === null || value === undefined) return null;
  if (field.mode === "REPEATED" && Array.isArray(value))
    return value.map((v) => recorded(v, { ...field, mode: "NULLABLE" }));
  switch (field.type) {
    case "TIMESTAMP":
      return { value: (value as string).replace("Z", ".000Z") };
    case "INT64":
    case "INTEGER":
      return typeof value === "number" ? value.toFixed(0) : value;
    case "JSON":
      return JSON.stringify(value);
    case "RECORD":
      return recordedRow(value as Record<string, unknown>, field.fields ?? []);
    default:
      return value;
  }
}
function recordedRow(row: Record<string, unknown>, fields: readonly BqField[]) {
  return Object.fromEntries(fields.map((f) => [f.name, recorded(row[f.name], f)]));
}
const tableRows = (table: BqTableName, rows: readonly object[]) =>
  rows.map((r) => recordedRow(r as Record<string, unknown>, bqTables[table].fields as readonly BqField[]));

interface Call {
  options: Record<string, unknown>;
}

/** Answers each named query (identified by its label) with recorded-shape rows. */
function stubClient(answers: Record<string, () => unknown[]>, bytes = 12_345_678) {
  const calls: Call[] = [];
  const client: BigQueryClient = {
    createQueryJob(options) {
      calls.push({ options });
      const label = (options.labels as { query: string }).query;
      const answer = Object.entries(answers).find(
        ([name]) => name.replace(/[^a-z0-9_-]/gi, "_").toLowerCase() === label,
      )?.[1];
      return Promise.resolve([
        {
          metadata: { statistics: { totalBytesProcessed: String(bytes) } },
          getQueryResults: () => Promise.resolve([answer ? answer() : []]),
        },
      ]);
    },
  };
  return { client, calls };
}

const data = getMockDataset();
const latest = new Map(data.registry.map((r) => [r.evaluation_id, r]));
const evalRow = latest.get("eval-0021")!;

describe("the BigQuery provider", { timeout: 60_000 }, () => {
  it("parameterizes every filter, dry-runs first and caps bytes on both jobs", async () => {
    const summaries = [...latest.values()]
      .slice(0, 3)
      .map(({ generation_params: _g, evaluation_params: _e, ...r }) => ({ ...r, total_rows: 40 }));
    const { client, calls } = stubClient({
      "evaluations.list": () =>
        summaries.map((r) =>
          recordedRow(r, [
            ...(bqTables.evaluation_data_history.fields as readonly BqField[]),
            { name: "total_rows", type: "INT64", mode: "NULLABLE" },
          ]),
        ),
    });
    const provider = new BigQueryProvider(config, () => Promise.resolve(client));
    const ctx = newContext();
    const injection = "x'; DROP TABLE t; --";
    const page = await provider.listEvaluations(
      {
        engine: ["b2_library"],
        q: injection,
        similarity_min: 0.3,
        sort: "overall_score",
        order: "asc",
        offset: 0,
        limit: 25,
      },
      ctx,
    );
    expect(page.total).toBe(40);
    expect(page.items).toHaveLength(3);
    expect(calls).toHaveLength(2);
    const [dry, run] = calls.map((c) => c.options);
    expect(dry!.dryRun).toBe(true);
    expect(run!.dryRun).toBeUndefined();
    for (const options of [dry!, run!]) {
      expect(options.maximumBytesBilled).toBe("1000000000");
      expect(options.useLegacySql).toBe(false);
      expect(options.location).toBe(config.BQ_LOCATION);
      const params = options.params as Record<string, unknown>;
      expect(params.engine).toEqual(["b2_library"]);
      expect(params.q).toBe(injection);
      expect(params.similarity_min).toBe(0.3);
      expect(params.tables).toEqual([]);
      expect(params.from).toBeNull();
      expect(Object.keys(options.types as object).sort()).toEqual(Object.keys(params).sort());
      const sql = options.query as string;
      expect(sql).not.toContain("b2_library");
      expect(sql).not.toContain("DROP");
      expect(sql).toContain("@engine");
      expect(sql).toContain("`demo-project.synthetic_data_quality.evaluation_latest`");
      expect(sql).toContain("ORDER BY overall_score ASC NULLS LAST");
    }
    expect(ctx.bytesEstimate).toBe(12_345_678);
    // The same query again is an LRU hit: no new job, no new bytes.
    const again = newContext();
    await provider.listEvaluations(
      {
        engine: ["b2_library"],
        q: injection,
        similarity_min: 0.3,
        sort: "overall_score",
        order: "asc",
        offset: 0,
        limit: 25,
      },
      again,
    );
    expect(calls).toHaveLength(2);
    expect(again).toEqual({ bytesEstimate: 0, queries: 1, cacheHits: 1 });
  });

  it("refuses a query the dry run prices above MAX_BYTES_BILLED, before running it", async () => {
    const { client, calls } = stubClient({}, 5_000_000_000);
    const provider = new BigQueryProvider(config, () => Promise.resolve(client));
    await expect(provider.runs({ limit: 10 })).rejects.toBeInstanceOf(BytesCapError);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.options.dryRun).toBe(true);
  });

  it("normalizes recorded rows and validates them (detail, runs, rag chunks)", async () => {
    const id = evalRow.evaluation_id;
    const { client } = stubClient({
      "evaluations.events": () =>
        tableRows(
          "evaluation_data_history",
          data.registry.filter((r) => r.evaluation_id === id),
        ),
      "metrics.byEvaluation": () =>
        tableRows(
          "evaluation_metrics",
          data.metrics.filter((m) => m.evaluation_id === id),
        ),
      "profiles.byEvaluation": () =>
        tableRows(
          "evaluation_profiles",
          data.profiles.filter((p) => p.evaluation_id === id),
        ),
      "flags.byEvaluation": () =>
        tableRows(
          "evaluation_row_flags",
          data.flags.filter((f) => f.evaluation_id === id),
        ),
      "runs.list": () => tableRows("validation_runs", data.validationRuns.slice(0, 5)),
    });
    const provider = new BigQueryProvider(config, () => Promise.resolve(client));
    const detail = await provider.getEvaluation(id);
    expect(evaluationDetailSchema.safeParse(detail).success).toBe(true);
    expect(detail!.evaluation.tables[0]!.rows_source).toBeTypeOf("number");
    expect(detail!.metrics.length).toBe(data.metrics.filter((m) => m.evaluation_id === id).length);
    expect(typeof detail!.profiles[0]!.payload).toBe("object");
    const runs = await provider.runs({ limit: 5 });
    expect(runs[0]!.dlq_by_rule_map).toEqual(JSON.parse(data.validationRuns[0]!.dlq_by_rule!));
  });

  it("turns chunk rows into metadata plus Float32 vectors", async () => {
    const set = data.rag[0]!;
    const rows = set.chunks
      .slice(0, 20)
      .map((c, i) => ({ ...c, embedding: Array.from(set.vectors.subarray(i * set.dim, (i + 1) * set.dim)) }));
    const { client, calls } = stubClient({ "rag.chunks": () => tableRows("rag_chunks", rows) });
    const provider = new BigQueryProvider(config, () => Promise.resolve(client));
    const chunks = await provider.ragChunks({
      digest: set.reference_digest,
      kind: "row_doc",
      embedder: "hashing-384/v1",
      limit: 20,
    });
    expect(chunks.dim).toBe(384);
    expect(chunks.meta).toHaveLength(20);
    expect(Array.from(chunks.vectors.subarray(0, 4))).toEqual(Array.from(set.vectors.subarray(0, 4)));
    const params = calls[1]!.options.params as Record<string, unknown>;
    expect(params).toMatchObject({ embedder: "hashing-384", version: "v1", kind: "row_doc", limit: 20 });
  });

  it("fails loudly when live rows drift from the generated contract", async () => {
    const drifted = data.validationRuns.slice(0, 2).map((r) => ({ ...r, status: "SOMETHING_NEW" }));
    const { client } = stubClient({ "runs.list": () => tableRows("validation_runs", drifted) });
    const provider = new BigQueryProvider(config, () => Promise.resolve(client));
    const error = await provider.runs({ limit: 5 }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ContractError);
    expect((error as ContractError).details.join(" ")).toContain("status");
  });

  it("builds facets from aggregate queries and stamps x-bq-bytes-estimate on routes", async () => {
    const { client } = stubClient({
      "evaluations.slim": () =>
        tableRows(
          "evaluation_data_history",
          [...latest.values()].map(({ generation_params: _g, evaluation_params: _e, ...r }) => r),
        ),
      "runs.count": () => [{ runs: "215" }],
      "metrics.ids": () => [{ metric_id: "column.ks", rows: "900" }],
      "sourceStats.tables": () => [
        {
          table_fqn: "demo-project.synthetic_source.users",
          tiers: ["sample", "exact"],
          latest_computed_at: { value: "2026-09-27T10:00:00.000Z" },
          columns: "12",
        },
      ],
      "rag.sets": () => [],
      "rag.poolSets": () => [],
    });
    const provider = new BigQueryProvider(config, () => Promise.resolve(client));
    const app = await buildApp({ config, provider, serveStatic: false });
    const res = await app.inject("/api/facets");
    expect(res.statusCode).toBe(200);
    expect(res.headers["x-data-source"]).toBe("bigquery");
    expect(Number(res.headers["x-bq-bytes-estimate"])).toBe(6 * 12_345_678);
    const facets = facetsSchema.parse(res.json());
    expect(facets.counts).toMatchObject({ evaluations: 40, runs: 215, metrics: 900 });
    const health = await app.inject("/api/health");
    expect(health.json()).toMatchObject({ mode: "bigquery", project: "demo-project" });
    await app.close();
  });
});

describe("the named-query registry", { timeout: 60_000 }, () => {
  const datasets = { quality: "demo-project.synthetic_data_quality", rag: "demo-project.synthetic_rag" };

  it("every query is one read-only SELECT whose @params are exactly its declared types", () => {
    for (const query of Object.values(QUERIES)) {
      const sql = query.sql(datasets, { sort: "evaluated_at", order: "desc" });
      expect(() => assertReadOnly(sql), query.name).not.toThrow();
      const used = new Set([...sql.matchAll(/@([a-z_0-9]+)/g)].map((m) => m[1]));
      expect([...used].sort(), query.name).toEqual(Object.keys(query.types).sort());
    }
  });

  it("assertReadOnly rejects anything that writes", () => {
    for (const sql of [
      "DELETE FROM t WHERE true",
      "SELECT 1; DROP TABLE t",
      "INSERT INTO t VALUES (1)",
      "CREATE TABLE x AS SELECT 1",
    ])
      expect(() => assertReadOnly(sql)).toThrow();
  });

  it("rejects a bigquery config without a project", () => {
    expect(() => loadConfig({ DATA_SOURCE: "bigquery" })).toThrow(/GCP_PROJECT/);
    expect(loadConfig({ GUI_SERVER_PORT: "8790" }).PORT).toBe(8790);
    expect(loadConfig({ PORT: "9000", GUI_SERVER_PORT: "8790" }).PORT).toBe(9000);
    expect(loadConfig({}).HOST).toBe("127.0.0.1");
  });
});
