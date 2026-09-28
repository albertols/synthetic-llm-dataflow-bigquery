/**
 * Every route in mock mode, through Fastify `inject`, validated against the
 * contract's zod. The same schemas guard the BigQuery provider, so a route
 * that passes here returns the same shapes live.
 */
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  apiErrorSchema,
  catalogueResponseSchema,
  chunkMetaSchema,
  comparisonSchema,
  decodeVectorEnvelope,
  dlqSummarySchema,
  evaluationDetailSchema,
  evaluationPageSchema,
  evaluationProfilesRowSchema,
  facetsSchema,
  freetextPoolSchema,
  healthSchema,
  knobsFileSchema,
  sourceStatsSchema,
  trendPointSchema,
  validationRunSchema,
  type ChunkMeta,
  type Facets,
} from "@synthetic-platform/contracts";
import type { FastifyInstance } from "fastify";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { z } from "zod";

import { buildApp } from "./app";
import { loadConfig } from "./config";
import { MockProvider } from "./providers/mock";

let app: FastifyInstance;
let staticDir: string;
let facets: Facets;

beforeAll(async () => {
  staticDir = mkdtempSync(join(tmpdir(), "spa-"));
  writeFileSync(join(staticDir, "index.html"), "<!doctype html><title>Synthetic Platform</title><div id=root></div>");
  const config = loadConfig({ LOG_LEVEL: "silent", STATIC_DIR: staticDir });
  app = await buildApp({ config, provider: new MockProvider() });
  facets = facetsSchema.parse((await app.inject("/api/facets")).json());
}, 120_000);

afterAll(async () => {
  await app.close();
  rmSync(staticDir, { recursive: true, force: true });
});

async function get<T>(url: string, schema: z.ZodType<T>, status = 200): Promise<T> {
  const res = await app.inject({ method: "GET", url });
  expect(res.statusCode, `${url}: ${res.body.slice(0, 300)}`).toBe(status);
  const parsed = schema.safeParse(res.json());
  expect(parsed.success, `${url}: ${parsed.success ? "" : JSON.stringify(parsed.error.issues.slice(0, 3))}`).toBe(true);
  return parsed.data!;
}

describe("the BFF in mock mode", { timeout: 60_000 }, () => {
  it("/api/health reports the data source and the bytes cap", async () => {
    const res = await app.inject("/api/health");
    expect(res.headers["x-data-source"]).toBe("mock");
    expect(res.headers["cache-control"]).toBe("no-store");
    const health = healthSchema.parse(res.json());
    expect(health.mode).toBe("mock");
    expect(health.project).toBeNull();
    expect(health.max_bytes_billed).toBe(10 * 1024 ** 3);
  });

  it("/api/facets lists every filter option", () => {
    expect(facets.counts.evaluations).toBe(40);
    expect(facets.engines).toEqual(["b1_rag", "b2_library"]);
    expect(facets.tables).toEqual(expect.arrayContaining(["users", "orders", "order_items", "user_features"]));
    expect(facets.latest?.evaluation_id).toBe("eval-0039");
    expect(facets.rag.length).toBeGreaterThan(0);
    expect(facets.source_tables.length).toBeGreaterThan(3);
  });

  it("/api/evaluations pages, filters and sorts", async () => {
    const first = await get("/api/evaluations?limit=10", evaluationPageSchema);
    expect(first.total).toBe(40);
    expect(first.items).toHaveLength(10);
    expect(first.items[0]!.evaluation_id).toBe("eval-0040");
    const b2 = await get("/api/evaluations?engine=b2_library&limit=500", evaluationPageSchema);
    expect(b2.items.length).toBeGreaterThan(3);
    expect(b2.items.every((e) => e.engine === "b2_library")).toBe(true);
    const sorted = await get("/api/evaluations?sort=overall_score&order=asc&limit=500", evaluationPageSchema);
    const scores = sorted.items.map((e) => e.overall_score).filter((s): s is number => s !== null);
    expect(scores).toEqual([...scores].sort((a, b) => a - b));
    expect(sorted.items.at(-1)!.overall_score).toBeNull();
    const byTable = await get("/api/evaluations?tables=user_features", evaluationPageSchema);
    expect(byTable.total).toBe(1);
    const text = await get("/api/evaluations?q=eval-0003", evaluationPageSchema);
    expect(text.items.map((e) => e.evaluation_id)).toEqual(["eval-0003"]);
    const window = await get("/api/evaluations?from=2026-09-01&to=2026-09-08&similarity_min=0.5", evaluationPageSchema);
    expect(
      window.items.every(
        (e) => e.evaluated_at >= "2026-09-01" && e.evaluated_at < "2026-09-08" && e.similarity! >= 0.5,
      ),
    ).toBe(true);
    await get("/api/evaluations?limit=0", apiErrorSchema, 400);
    await get("/api/evaluations?sort=nope", apiErrorSchema, 400);
  });

  it("/api/evaluations/:id returns the registry events, metrics, profiles and flags", async () => {
    const detail = await get("/api/evaluations/eval-0001", evaluationDetailSchema);
    expect(detail.events.map((e) => e.event)).toEqual(["RUNNING", "FINAL"]);
    expect(detail.evaluation.event).toBe("FINAL");
    expect(detail.metrics.length).toBeGreaterThan(100);
    expect(detail.profiles.length).toBeGreaterThan(50);
    expect(detail.flags.length).toBeGreaterThan(0);
    const light = await get("/api/evaluations/eval-0001?profiles=none", evaluationDetailSchema);
    expect(light.profiles).toEqual([]);
    const running = await get("/api/evaluations/eval-0040", evaluationDetailSchema);
    expect(running.evaluation.status).toBe("RUNNING");
    expect(running.metrics).toEqual([]);
    const wide = await get("/api/evaluations/eval-0032", evaluationDetailSchema);
    expect(new Set(wide.metrics.map((m) => m.column_name).filter(Boolean)).size).toBe(200);
    await get("/api/evaluations/nope", apiErrorSchema, 404);
    const profiles = await get(
      "/api/evaluations/eval-0021/profiles?table=orders&column=created_at&kind=histogram,quantiles",
      z.array(evaluationProfilesRowSchema),
    );
    expect(new Set(profiles.map((p) => p.profile_kind))).toEqual(new Set(["histogram", "quantiles"]));
    await get("/api/evaluations/nope/profiles", apiErrorSchema, 404);
  });

  it("/api/compare aligns metrics and flags what is not comparable", async () => {
    const cmp = await get("/api/compare?ids=eval-0001,eval-0036,missing-id", comparisonSchema);
    expect(cmp.evaluations.map((e) => e.evaluation_id)).toEqual(["eval-0001", "eval-0036"]);
    expect(cmp.missing).toEqual(["missing-id"]);
    expect(cmp.metrics.every((m) => m.cells.length === 2)).toBe(true);
    expect(cmp.params.find((p) => p.path === "freetext_expansion")?.differs).toBe(true);
    expect(cmp.comparability.evaluator_version_same).toBe(false);
    expect(cmp.comparability.not_comparable.length).toBeGreaterThan(0);
    const same = await get("/api/compare?ids=eval-0021,eval-0036", comparisonSchema);
    expect(same.comparability.not_comparable).toEqual([]);
    await get("/api/compare", apiErrorSchema, 400);
  });

  it("/api/trend returns one metric across evaluations, oldest first", async () => {
    const points = await get(
      "/api/trend?metric_id=column.ks&table=orders&column=created_at",
      z.array(trendPointSchema),
    );
    expect(points.length).toBeGreaterThan(20);
    expect(points.map((p) => p.evaluated_at)).toEqual([...points.map((p) => p.evaluated_at)].sort());
    const b1 = await get("/api/trend?metric_id=row.memorization_lift&engine=b1_rag", z.array(trendPointSchema));
    expect(b1.every((p) => p.engine === "b1_rag")).toBe(true);
    await get("/api/trend", apiErrorSchema, 400);
  });

  it("/api/runs and /api/dlq, with BLOCKER accounting from the code", async () => {
    const runs = await get("/api/runs?limit=1000", z.array(validationRunSchema));
    expect(runs.length).toBeGreaterThan(100);
    expect(runs.some((r) => r.status === "FAILED_BLOCKER")).toBe(true);
    const orphanRun = runs.find((r) => (r.dlq_by_rule_map["fk.orphan"] ?? 0) > 0)!;
    const dlq = await get(`/api/dlq?run_ids=${orphanRun.run_id}`, z.array(dlqSummarySchema));
    const orphan = dlq.find((d) => d.rule_id === "fk.orphan")!;
    expect(orphan.count).toBe(orphanRun.dlq_by_rule_map["fk.orphan"]);
    expect(orphan.blocker_declared).toBe(true);
    expect(orphan.blocker_counted).toBe(false);
    const filtered = await get("/api/runs?engine=b2_library&status=PASSED", z.array(validationRunSchema));
    expect(filtered.every((r) => r.engine === "b2_library" && r.status === "PASSED")).toBe(true);
    await get("/api/dlq", apiErrorSchema, 400);
  });

  it("/api/source-stats returns both tiers, parsed", async () => {
    const stats = await get("/api/source-stats?table=users", sourceStatsSchema);
    expect(stats.table_fqn).toBe("demo-project.synthetic_source.users");
    expect(stats.tiers_available).toEqual(["exact", "sample"]);
    expect(stats.selected).toHaveLength(2);
    expect(stats.columns.every((c) => c.stats_parsed !== null)).toBe(true);
    const exact = await get("/api/source-stats?table=users&tier=exact", sourceStatsSchema);
    expect(new Set(exact.columns.map((c) => c.stats_tier))).toEqual(new Set(["exact"]));
    await get("/api/source-stats?table=nope", apiErrorSchema, 404);
  });

  it("/api/rag/chunks streams Float32 vectors with their metadata (≤ 5 MB)", async () => {
    const set = facets.rag.find((s) => s.embedder_id === "hashing-384" && s.source_fqn.endsWith(".users"))!;
    const res = await app.inject(
      `/api/rag/chunks?digest=${set.reference_digest}&kind=row_doc&embedder=hashing-384&limit=1024`,
    );
    expect(res.statusCode).toBe(200);
    expect(res.headers["content-type"]).toBe("application/octet-stream");
    expect(res.rawPayload.length).toBeLessThan(5 * 1024 * 1024);
    const envelope = decodeVectorEnvelope<ChunkMeta>(res.rawPayload);
    expect(envelope.dim).toBe(384);
    expect(envelope.count).toBe(1024);
    expect(res.headers["x-vector-count"]).toBe("1024");
    for (const meta of envelope.meta.slice(0, 50)) expect(chunkMetaSchema.safeParse(meta).success).toBe(true);
    expect(Math.hypot(...envelope.vectors.subarray(0, 384))).toBeCloseTo(1, 4);
    const values = decodeVectorEnvelope<ChunkMeta>(
      (
        await app.inject(
          `/api/rag/chunks?digest=${set.reference_digest}&kind=free_text_col&embedder=hashing-384/v1&column=city`,
        )
      ).rawPayload,
    );
    expect(values.count).toBeGreaterThan(10);
    expect(values.meta.every((m) => m.column === "city")).toBe(true);
    await get("/api/rag/chunks?kind=row_doc", apiErrorSchema, 400);
  });

  it("/api/rag/pools returns the pools of a digest", async () => {
    const pool = facets.pools[0]!;
    const pools = await get(`/api/rag/pools?digest=${pool.reference_digest}`, z.array(freetextPoolSchema));
    expect(pools.length).toBeGreaterThan(0);
    expect(pools.every((p) => p.distinct === p.values.length && p.values.length <= 512)).toBe(true);
  });

  it("/api/knobs and /api/catalogue serve the generated contracts", async () => {
    const knobs = await get("/api/knobs", knobsFileSchema);
    expect(knobs.channels).toHaveLength(8);
    const catalogue = await get("/api/catalogue", catalogueResponseSchema);
    expect(catalogue.metrics).toHaveLength(79);
  });

  it("unknown API paths are JSON 404s; client routes fall back to the SPA", async () => {
    await get("/api/nope", apiErrorSchema, 404);
    const spa = await app.inject("/evaluation/eval-0001?tab=privacy");
    expect(spa.statusCode).toBe(200);
    expect(spa.headers["content-type"]).toContain("text/html");
    expect(spa.body).toContain("Synthetic Platform");
    expect((await app.inject({ method: "POST", url: "/api/evaluations" })).statusCode).toBe(404);
  });
});
