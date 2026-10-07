/**
 * Every mock row must be a row the real schema allows: the generated zod for
 * all ten BigQuery tables, the profile payload shapes, the profiler entry, the
 * catalogue's vocabulary, and the repo's sensitive-content rules (no 15–16
 * digit integers, e-mails only at example.com).
 */
import { beforeAll, describe, expect, it } from "vitest";

import {
  catalogueById,
  dlqRowSchema,
  evaluationDataHistoryRowSchema,
  evaluationMetricsRowSchema,
  evaluationProfilesRowSchema,
  evaluationRowFlagsRowSchema,
  fkFanoutStatsRowSchema,
  freetextPoolsRowSchema,
  profilePayloadSchemas,
  profilerStatsSchema,
  ragChunksRowSchema,
  sourceTableStatsRowSchema,
  validationRunsRowSchema,
  vocabularies,
  type MetricId,
} from "@synthetic-platform/contracts";

import { chunkRow, createMockDataset, type MockDataset } from "./index";

let data: MockDataset;
beforeAll(() => {
  data = createMockDataset();
}, 120_000);

function expectAll<T>(label: string, rows: readonly T[], parse: (row: T) => { success: boolean; error?: unknown }) {
  const failures: string[] = [];
  rows.forEach((row, i) => {
    const result = parse(row);
    if (!result.success && failures.length < 3)
      failures.push(`${label}[${i}]: ${JSON.stringify(result.error).slice(0, 400)}`);
  });
  expect(failures).toEqual([]);
}

/** A 15–16 digit INTEGER run (the precheck's card rule); fractional digits of a float are not identifiers. */
const CARD_LIKE = /(?<![\w.-])\d{15,16}(?![\w.-])/;
const EMAIL = /[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})/g;
/** A character mask of an address (9 digit, A upper, a lower), as shape profiles store it — not an address. */
const isMask = (text: string) => /^[aA9._%+@-]+$/.test(text);

describe("mock rows validate against the generated contracts", { timeout: 60_000 }, () => {
  it("registry events", () => {
    expect(data.registry.length).toBeGreaterThan(40);
    expectAll("registry", data.registry, (r) => evaluationDataHistoryRowSchema.safeParse(r));
  });

  it("metric rows, with catalogue ids, levels and families", () => {
    expect(data.metrics.length).toBeGreaterThan(5000);
    expectAll("metrics", data.metrics, (r) => evaluationMetricsRowSchema.safeParse(r));
    for (const row of data.metrics) {
      const metric = catalogueById[row.metric_id as MetricId];
      expect(metric, row.metric_id).toBeDefined();
      expect(row.level).toBe(metric.level);
      expect(row.family).toBe(metric.family);
      if (row.value !== null) expect(Number.isFinite(row.value)).toBe(true);
    }
  });

  it("profile rows and every payload shape", () => {
    expectAll("profiles", data.profiles, (r) => evaluationProfilesRowSchema.safeParse(r));
    expectAll("payloads", data.profiles, (r) => profilePayloadSchemas[r.profile_kind].safeParse(r.payload));
    const kinds = new Set(data.profiles.map((p) => p.profile_kind));
    for (const kind of Object.keys(profilePayloadSchemas)) expect(kinds.has(kind as never), kind).toBe(true);
    // One payload contract per kind the evaluator's schema names, and none it does not.
    expect(Object.keys(profilePayloadSchemas).sort()).toEqual(
      [...vocabularies["evaluation_profiles.profile_kind"]].sort(),
    );
  });

  it("row flags, validation runs, the DLQ and fan-out stats", () => {
    expectAll("flags", data.flags, (r) => evaluationRowFlagsRowSchema.safeParse(r));
    expectAll("validation_runs", data.validationRuns, (r) => validationRunsRowSchema.safeParse(r));
    expectAll("dlq", data.dlq, (r) => dlqRowSchema.safeParse(r));
    expectAll("fk_fanout_stats", data.fanoutStats, (r) => fkFanoutStatsRowSchema.safeParse(r));
    expect(data.flags.every((f) => f.source_key === null)).toBe(true);
  });

  it("source-table stats and their profiler entries", () => {
    expectAll("source_table_stats", data.sourceStats, (r) => sourceTableStatsRowSchema.safeParse(r));
    expectAll("stats", data.sourceStats, (r) => profilerStatsSchema.safeParse(JSON.parse(r.stats ?? "null")));
    // NULL = the legacy profiler-"1" snapshot (read as sample).
    expect(new Set(data.sourceStats.map((r) => r.stats_tier))).toEqual(new Set(["sample", "exact", null]));
  });

  it("pools and RAG chunks (Float32 unit vectors, ≤ 1,024 row docs per set)", () => {
    expectAll("freetext_pools", data.pools, (r) => freetextPoolsRowSchema.safeParse(r));
    for (const set of data.rag) {
      expect(set.vectors.length).toBe(set.chunks.length * set.dim);
      expect(set.chunks.filter((c) => c.chunk_kind === "row_doc").length).toBeLessThanOrEqual(1024);
      for (let i = 0; i < set.chunks.length; i += 37) {
        expect(ragChunksRowSchema.safeParse(chunkRow(set, i)).success).toBe(true);
        const v = set.vectors.subarray(i * set.dim, (i + 1) * set.dim);
        expect(Math.hypot(...v)).toBeCloseTo(1, 4);
      }
    }
  });

  it("never carries card-shaped digit runs or non-example e-mail addresses", () => {
    const tables = [
      data.registry,
      data.metrics,
      data.profiles,
      data.flags,
      data.validationRuns,
      data.dlq,
      data.sourceStats,
      data.pools,
    ];
    for (const table of tables) {
      const text = JSON.stringify(table);
      expect(CARD_LIKE.test(text)).toBe(false);
      for (const match of text.matchAll(EMAIL))
        if (!isMask(match[0])) expect(match[1]!.toLowerCase()).toBe("example.com");
    }
    const chunkText = data.rag.flatMap((s) => s.chunks.map((c) => c.chunk_text)).join("\n");
    expect(CARD_LIKE.test(chunkText)).toBe(false);
    for (const match of chunkText.matchAll(EMAIL)) expect(match[1]!.toLowerCase()).toBe("example.com");
  });
});
