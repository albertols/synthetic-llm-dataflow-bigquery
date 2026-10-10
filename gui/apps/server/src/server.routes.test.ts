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
  relationshipsResponseSchema,
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
  facets = facetsSchema.parse((await inject("/api/facets")).json());
  // Building the seeded mock dataset is CPU-bound: 120 s ran out once at load average ~450.
}, 300_000);

/** Requests arrive with the Host a browser on this machine sends (the guard refuses others). */
function inject(url: string, headers: Record<string, string> = {}, method: "GET" | "POST" = "GET") {
  return app.inject({ method, url, headers: { host: "127.0.0.1:8787", ...headers } });
}

afterAll(async () => {
  await app.close();
  rmSync(staticDir, { recursive: true, force: true });
});

async function get<T>(url: string, schema: z.ZodType<T>, status = 200): Promise<T> {
  const res = await inject(url);
  expect(res.statusCode, `${url}: ${res.body.slice(0, 300)}`).toBe(status);
  const parsed = schema.safeParse(res.json());
  expect(parsed.success, `${url}: ${parsed.success ? "" : JSON.stringify(parsed.error.issues.slice(0, 3))}`).toBe(true);
  return parsed.data!;
}

describe("the BFF in mock mode", { timeout: 60_000 }, () => {
  it("/api/health reports the data source and the bytes cap", async () => {
    const res = await inject("/api/health");
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
    // A page past the end still reports the total.
    const past = await get("/api/evaluations?offset=100&limit=10", evaluationPageSchema);
    expect([past.items.length, past.total]).toEqual([0, 40]);
    // from/to are ISO timestamps (with offset) or dates; anything else is a 400.
    await get("/api/evaluations?from=yesterday", apiErrorSchema, 400);
    await get("/api/evaluations?to=2026-09-01T10:00:00", apiErrorSchema, 400);
  });

  it("timestamps keep their microseconds end to end", async () => {
    const all = await get("/api/evaluations?limit=500", evaluationPageSchema);
    const e = all.items.find((x) => x.evaluation_id === "eval-0010")!;
    expect(e.evaluated_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/);
    expect(e.evaluated_at.endsWith("000Z")).toBe(false);
    const oneMicroLater = e.evaluated_at.replace(
      /(\d{6})Z$/,
      (_, d: string) => `${String(Number(d) + 1).padStart(6, "0")}Z`,
    );
    const from = await get(`/api/evaluations?limit=500&from=${e.evaluated_at}`, evaluationPageSchema);
    expect(from.items.some((x) => x.evaluation_id === "eval-0010")).toBe(true);
    const after = await get(`/api/evaluations?limit=500&from=${oneMicroLater}`, evaluationPageSchema);
    expect(after.items.some((x) => x.evaluation_id === "eval-0010")).toBe(false);
  });

  it("/api/evaluations/:id returns the registry events, metrics, profiles and flags", async () => {
    const detail = await get("/api/evaluations/eval-0001", evaluationDetailSchema);
    expect(detail.events.map((e) => e.event)).toEqual(["RUNNING", "FINAL"]);
    expect(detail.evaluation.event).toBe("FINAL");
    expect(detail.metrics.length).toBeGreaterThan(100);
    expect(detail.flags.length).toBeGreaterThan(0);
    // Profiles are opt-in (`?profiles=all`); a detail page loads them per drawer.
    expect(detail.profiles).toEqual([]);
    const full = await get("/api/evaluations/eval-0001?profiles=all", evaluationDetailSchema);
    expect(full.profiles.length).toBeGreaterThan(50);
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
    expect(cmp.comparability.catalogue_version_same).toBe(false);
    const reasons = cmp.comparability.not_comparable.join("\n");
    expect(reasons).toContain("catalogue_version differs (0.9.0 vs 1.0.0)");
    expect(reasons).toContain("evaluator_version differs (0.1.0 vs 0.2.0)");
    expect(reasons).toContain("encoding_plan_digest differs");
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
    // `limit` keeps the NEWEST points, still oldest first.
    const newest = await get(
      "/api/trend?metric_id=column.ks&table=orders&column=created_at&limit=5",
      z.array(trendPointSchema),
    );
    expect(newest.map((p) => p.evaluation_id)).toEqual(points.slice(-5).map((p) => p.evaluation_id));
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
    // A launch's per-table rows are <base>-NN-<table>; a one-table launch writes <base>.
    const launch = await get("/api/evaluations/eval-0021", evaluationDetailSchema);
    const base = launch.evaluation.base_run_id!;
    const perTable = await get(`/api/runs?base_run_id=${base}`, z.array(validationRunSchema));
    expect(perTable.map((r) => r.run_id).sort()).toEqual(
      [`${base}-00-users`, `${base}-01-orders`, `${base}-02-order_items`].sort(),
    );
    const wide = await get("/api/evaluations/eval-0032", evaluationDetailSchema);
    const single = await get(`/api/runs?base_run_id=${wide.evaluation.base_run_id!}`, z.array(validationRunSchema));
    expect(single.map((r) => r.run_id)).toEqual([wide.evaluation.base_run_id]);
    const filtered = await get("/api/runs?engine=b2_library&status=PASSED", z.array(validationRunSchema));
    expect(filtered.every((r) => r.engine === "b2_library" && r.status === "PASSED")).toBe(true);
    await get("/api/dlq", apiErrorSchema, 400);
  });

  it("/api/source-stats keys snapshots by (digest, tier, profiler_version, run_id)", async () => {
    const stats = await get("/api/source-stats?table=users", sourceStatsSchema);
    expect(stats.table_fqn).toBe("demo-project.synthetic_source.users");
    expect(stats.tiers_available).toEqual(["exact", "sample"]);
    expect(stats.selected).toHaveLength(2);
    expect(stats.columns.every((c) => c.stats_parsed !== null && stats.selected.includes(c.snapshot_key))).toBe(true);
    const exact = await get("/api/source-stats?table=users&tier=exact", sourceStatsSchema);
    expect(new Set(exact.columns.map((c) => c.stats_tier))).toEqual(new Set(["exact"]));
    // One digest profiled on both tiers: the tier compare of one reference sample.
    const shared = stats.snapshots.find((s) =>
      stats.snapshots.some((o) => o.reference_digest === s.reference_digest && o.tier !== s.tier),
    )!;
    const both = await get(`/api/source-stats?table=users&digest=${shared.reference_digest}`, sourceStatsSchema);
    expect(both.selected).toHaveLength(2);
    expect(new Set(both.columns.map((c) => c.tier))).toEqual(new Set(["sample", "exact"]));
    expect(new Set(both.columns.map((c) => c.reference_digest))).toEqual(new Set([shared.reference_digest]));
    // The legacy snapshot (NULL stats_tier) reads as sample and still parses.
    const legacy = stats.snapshots.find((s) => s.legacy_tier)!;
    expect(legacy.tier).toBe("sample");
    const old = await get(
      `/api/source-stats?table=users&snapshot=${encodeURIComponent(legacy.key)}`,
      sourceStatsSchema,
    );
    expect(old.selected).toEqual([legacy.key]);
    expect(old.columns.every((c) => c.stats_tier === null && c.tier === "sample" && c.stats_parsed !== null)).toBe(
      true,
    );
    await get("/api/source-stats?table=nope", apiErrorSchema, 404);
  });

  it("/api/rag/chunks streams Float32 vectors with their metadata (≤ 5 MB)", async () => {
    const set = facets.rag.find((s) => s.embedder_id === "hashing-384" && s.source_fqn.endsWith(".users"))!;
    const res = await inject(
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
    expect(envelope.meta.some((m) => "source_pk" in m)).toBe(false);
    expect(Math.hypot(...envelope.vectors.subarray(0, 384))).toBeCloseTo(1, 4);
    const values = decodeVectorEnvelope<ChunkMeta>(
      (
        await inject(
          `/api/rag/chunks?digest=${set.reference_digest}&kind=free_text_col&embedder=hashing-384/v1&column=city`,
        )
      ).rawPayload,
    );
    expect(values.count).toBeGreaterThan(10);
    expect(values.meta.every((m) => m.column === "city")).toBe(true);
    await get("/api/rag/chunks?kind=row_doc", apiErrorSchema, 400);
    await get(
      `/api/rag/chunks?digest=${set.reference_digest}&kind=row_doc&embedder=hashing-384&limit=3001`,
      apiErrorSchema,
      400,
    );
  });

  it("/api/rag/pools returns the pools of a digest; facets name each pool's table", async () => {
    expect(facets.pools.length).toBeGreaterThan(0);
    expect(facets.pools.every((p) => p.table_fqn?.startsWith("demo-project.synthetic_source."))).toBe(true);
    const pool = facets.pools[0]!;
    const pools = await get(`/api/rag/pools?digest=${pool.reference_digest}`, z.array(freetextPoolSchema));
    expect(pools.length).toBeGreaterThan(0);
    expect(pools.every((p) => p.distinct === p.values.length && p.values.length <= 512)).toBe(true);
  });

  it("/api/relationships serves the committed examples and, in mock mode, the mock's model", async () => {
    const { models } = await get("/api/relationships", relationshipsResponseSchema);
    expect(models.find((m) => m.model === "gcp_public_thelook")?.origin).toBe("committed_example");
    const demo = models.find((m) => m.model === facets.relationship_models[0])!;
    expect(demo.origin).toBe("mock");
    const documented = demo.tables.flatMap((t) => t.fk).filter((e) => e.role === "documented");
    expect(documented.map((e) => e.enforced)).toEqual([false]);
  });

  it("refuses a foreign Host (DNS rebinding) and cross-site API requests", async () => {
    for (const host of ["evil.example.com", "evil.example.com:8787", "127.0.0.1:9999", ""]) {
      const res = await app.inject({ method: "GET", url: "/api/health", headers: { host } });
      expect(res.statusCode, host).toBe(403);
      expect(apiErrorSchema.safeParse(res.json()).success).toBe(true);
    }
    expect((await app.inject({ url: "/", headers: { host: "rebind.example.com:8787" } })).statusCode).toBe(403);
    // Loopback names on the BFF port, and the Vite dev server's Host (its proxy forwards it).
    for (const host of ["localhost:8787", "127.0.0.1:8787", "127.0.0.1:5173", "localhost:5173"])
      expect((await inject("/api/health", { host })).statusCode, host).toBe(200);
    expect((await inject("/api/facets", { "sec-fetch-site": "cross-site" })).statusCode).toBe(403);
    expect((await inject("/api/facets", { "sec-fetch-site": "same-origin" })).statusCode).toBe(200);
    // Same-site is another port (or subdomain) of this host: another local app, refused like cross-site.
    expect((await inject("/api/facets", { "sec-fetch-site": "same-site" })).statusCode).toBe(403);
    expect((await inject("/api/facets", { "sec-fetch-site": "none" })).statusCode).toBe(200);
    expect((await inject("/api/health", { origin: "https://evil.example.com" })).statusCode).toBe(403);
    expect((await inject("/api/health", { origin: "null" })).statusCode).toBe(403);
    expect((await inject("/api/health", { origin: "http://127.0.0.1:5173" })).statusCode).toBe(200);
    // Top-level navigation to the SPA is not an API call.
    expect((await inject("/", { "sec-fetch-site": "cross-site" })).statusCode).toBe(200);
  });

  it("/api/knobs and /api/catalogue serve the generated contracts", async () => {
    const knobs = await get("/api/knobs", knobsFileSchema);
    expect(knobs.channels).toHaveLength(8);
    const catalogue = await get("/api/catalogue", catalogueResponseSchema);
    expect(catalogue.metrics).toHaveLength(79);
  });

  it("unknown API paths are JSON 404s; client routes fall back to the SPA", async () => {
    await get("/api/nope", apiErrorSchema, 404);
    const spa = await inject("/evaluation/eval-0001?tab=privacy");
    expect(spa.statusCode).toBe(200);
    expect(spa.headers["content-type"]).toContain("text/html");
    expect(spa.body).toContain("Synthetic Platform");
    expect((await inject("/api/evaluations", {}, "POST")).statusCode).toBe(404);
  });
});
