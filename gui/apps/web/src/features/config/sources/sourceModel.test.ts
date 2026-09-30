import { describe, expect, it } from "vitest";

import type { SourceStatsColumn, SourceStatsSnapshot } from "@contracts/api";
import { dkwEpsilon } from "@synthetic-platform/stats";

import {
  compareTiers,
  decileValueCount,
  decodeNullPattern,
  digestsWithBothTiers,
  isHashedLabel,
  quantileAt,
  quantileRows,
  shareInterval,
} from "./sourceModel";

const snap = (digest: string, tier: "sample" | "exact"): SourceStatsSnapshot => ({
  key: `${digest}|${tier}|2|run`,
  reference_digest: digest,
  run_id: "run",
  tier,
  legacy_tier: false,
  profiler_version: "2",
  sample_rows: tier === "exact" ? 100_000 : 10_000,
  computed_at: "2026-08-03T04:52:00.104318Z",
  columns: 1,
});

function col(key: string, column: string, tier: "sample" | "exact", extra: Partial<SourceStatsColumn> = {}) {
  return {
    table_fqn: "demo-project.synthetic_source.users",
    reference_digest: key.split("|")[0]!,
    run_id: "run",
    column,
    generation_plan: "categorical",
    null_fraction: 0.01,
    empty_fraction: 0,
    distinct: 10,
    distinct_ratio: 0.001,
    is_pk: false,
    is_fk: false,
    stats: null,
    sample_rows: tier === "exact" ? 100_000 : 10_000,
    stats_tier: tier,
    profiler_version: "2",
    computed_at: "2026-08-03T04:52:00.104318Z",
    snapshot_key: key,
    tier,
    stats_parsed: null,
    ...extra,
  };
}

describe("source-stats helpers", () => {
  it("finds the digests profiled on both tiers, newest first", () => {
    expect(
      digestsWithBothTiers([snap("b", "exact"), snap("a", "exact"), snap("a", "sample"), snap("c", "sample")]),
    ).toEqual(["a"]);
    expect(digestsWithBothTiers([])).toEqual([]);
  });

  it("interpolates the 11-point decile vector and refuses degenerate vectors", () => {
    const deciles = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100];
    expect(quantileAt(deciles, 0.25)).toBeCloseTo(25, 9);
    expect(quantileAt(deciles, -1)).toBe(0);
    expect(quantileAt(deciles, 2)).toBe(100);
    expect(quantileAt([], 0.5)).toBeNull();
    expect(quantileAt([1, Number.NaN, 3], 0.5)).toBeNull();
  });

  it("puts the DKW band around the sample tier's quantile function, in rank space", () => {
    const deciles = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100];
    const rows = quantileRows({ deciles, rows: 10_000 }, { deciles });
    const eps = dkwEpsilon(10_000);
    const mid = rows.find((r) => r.p === 0.5)!;
    expect(mid.sample).toBeCloseTo(50, 9);
    expect(mid.bandBase).toBeCloseTo(100 * (0.5 - eps), 6);
    expect(mid.bandBase! + mid.bandSpan!).toBeCloseTo(100 * (0.5 + eps), 6);
    expect(quantileRows(null, { deciles }).every((r) => r.bandBase === null && r.sample === null)).toBe(true);
  });

  it("gives the sample tier a Wilson interval and the exact tier none", () => {
    const s = shareInterval(0.01, 10_000, "sample")!;
    expect(s.low).toBeLessThan(0.01);
    expect(s.high).toBeGreaterThan(0.01);
    expect(shareInterval(0.01, 100_000, "exact")).toEqual({ share: 0.01, low: null, high: null });
    expect(shareInterval(null, 10, "sample")).toBeNull();
  });

  it("decodes row null patterns and recognises hashed labels", () => {
    expect(decodeNullPattern("00000010000", ["id", "a", "b", "c", "d", "e", "state", "f", "g", "h", "i"])).toEqual([
      "state",
    ]);
    expect(decodeNullPattern("000", ["a", "b", "c"])).toEqual([]);
    expect(isHashedLabel("h:0a1b2c3d")).toBe(true);
    expect(isHashedLabel("China")).toBe(false);
  });

  it("compares one digest on both tiers: truncation and the DKW band on null rates", () => {
    const sk = "d|sample|2|run";
    const ek = "d|exact|2|run";
    const rows = compareTiers(
      [
        col(sk, "email", "sample", { distinct: 6_187, null_fraction: 0.01 }),
        col(ek, "email", "exact", { distinct: 99_867, null_fraction: 0.05 }),
        col(sk, "gender", "sample", { distinct: 2, null_fraction: 0.001 }),
        col(ek, "gender", "exact", { distinct: 2, null_fraction: 0.002 }),
        col(sk, "__table__", "sample"),
        col(ek, "only_exact", "exact", { distinct: null, null_fraction: null }),
      ],
      sk,
      ek,
      10_000,
    );
    expect(rows.map((r) => r.column)).toEqual(["email", "gender", "only_exact"]);
    expect(rows[0]!.truncation).toBeCloseTo(99_867 / 6_187, 9);
    expect(rows[0]!.nullWithinBand).toBe(false);
    expect(rows[1]!.nullWithinBand).toBe(true);
    expect(rows[2]).toMatchObject({ sampleDistinct: null, truncation: null, nullWithinBand: null });
  });

  it("sizes a decile band by the values the deciles are computed over, not all rows", () => {
    expect(decileValueCount({ sample_rows: 10_000, null_fraction: 0.3, empty_fraction: 0.1 })).toBe(6_000);
    expect(decileValueCount({ sample_rows: 10_000, null_fraction: null, empty_fraction: null })).toBe(10_000);
    expect(decileValueCount({ sample_rows: null, null_fraction: 0, empty_fraction: 0 })).toBe(0);
    expect(decileValueCount({ sample_rows: 100, null_fraction: 0.8, empty_fraction: 0.5 })).toBe(0);
  });
});
