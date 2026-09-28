import { describe, expect, it } from "vitest";

import { dkwEpsilon } from "@synthetic-platform/stats";

import {
  computeScenario,
  CONSTANTS,
  duplicateShare,
  effectiveBatchSize,
  estimateTime,
  expectedDistinctUniform,
  poolTarget,
  poolDistinct,
  PRESETS,
  presetById,
  recommend,
  rowsForTail,
  rowsToSee,
} from "./scenario";

const preset90m = presetById("90m-from-1m").inputs;

describe("scenario formulas", () => {
  it("DKW band at n = 10,000 and α = 0.05 is 0.0136 (design 2026-07-24 §3.1)", () => {
    expect(dkwEpsilon(10_000, 0.05)).toBeCloseTo(0.01358, 5);
    expect(dkwEpsilon(10_000, 0.05).toFixed(4)).toBe("0.0136");
    // n ≥ ln(40)/(2ε²): the design's table, ε = 1% needs ~18,400 rows.
    expect(Math.log(40) / (2 * 0.01 ** 2)).toBeCloseTo(18_444, 0);
  });

  it("rows to see a share-p category ≈ 3/p, for a 10 % relative SE 100/p", () => {
    expect(rowsToSee(0.001)).toBe(2_995);
    expect(rowsToSee(0.01)).toBe(299);
    expect(rowsForTail(0.999)).toBe(20_000);
  });

  it("random key draws at 10× capacity duplicate ~4.8 % (pk_capacity.py comment)", () => {
    expect(CONSTANTS.keyMargin).toBe(10);
    expect(duplicateShare(1_000_000, 10 * 1_000_000)).toBeCloseTo(0.04837, 4);
  });

  it("a PK routed to a 512-value pool loses M − 512 rows (the 2026-08-21 run: 999,488 of 1M)", () => {
    const out = computeScenario({ ...preset90m, M: 1_000_000 });
    expect(out.pkPoolDlq).toBe(999_488);
  });

  it("the pool target is min(num_rows, distinct, 512)", () => {
    expect(poolTarget(90_000_000, 4_022)).toBe(512);
    expect(poolTarget(90_000_000, 94)).toBe(94);
    expect(poolTarget(100, 4_022)).toBe(100);
    expect(poolTarget(0, 0)).toBe(512);
  });

  it("batch size scales toward ~1,000 elements, never below 16, unless pinned", () => {
    expect(effectiveBatchSize(16, 90_000_000)).toBe(90_000);
    expect(effectiveBatchSize(16, 5_000)).toBe(16);
    expect(effectiveBatchSize(64, 90_000_000)).toBe(64);
  });

  it("expected distinct in m uniform draws never exceeds D or m", () => {
    expect(expectedDistinctUniform(4_022, 500)).toBeCloseTo(470.2, 0);
    expect(expectedDistinctUniform(10, 1_000_000)).toBeCloseTo(10, 6);
    expect(expectedDistinctUniform(1_000_000, 10)).toBeCloseTo(10, 3);
  });
});

describe("the 90M / 1M / 10k preset", () => {
  const out = computeScenario(preset90m);

  it("starts from the brief's numbers", () => {
    expect(PRESETS[0]!.id).toBe("90m-from-1m");
    expect(preset90m).toMatchObject({ N: 1_000_000, M: 90_000_000, n: 10_000 });
    expect(presetById("nope").id).toBe("90m-from-1m");
  });

  it("amplifies 9,000× over the sample and 90× over the source", () => {
    expect(out.ampSample).toBe(9_000);
    expect(out.ampSource).toBe(90);
    expect(out.sampleShare).toBeCloseTo(0.01, 12);
    expect(out.census).toBe(false);
  });

  it("carries the DKW band, rare capture and tail support of a 10k sample", () => {
    expect(out.epsilon.toFixed(4)).toBe("0.0136");
    expect(out.thresholdFloor).toBeCloseTo(0.02716, 4);
    // 1 − 0.999^10000: a 0.1 % category is present (≈ 10 rows) with ≈ 99.995 % probability.
    expect(out.rareCapture).toBeCloseTo(0.999955, 5);
    expect(out.rareExpectedRows).toBeCloseTo(10, 9);
    // n(1 − q): p99.9 rests on 10 points.
    expect(out.tailPoints).toBeCloseTo(10, 9);
  });

  it("reuses each of the 512 pool values ≈ 175,781 times", () => {
    expect(CONSTANTS.poolCap).toBe(512);
    expect(out.poolReuse).toBeCloseTo(175_781.25, 6);
    expect(out.rowDocShare).toBeCloseTo(1_024 / 10_000, 12);
  });

  it("truncates the sparse column's distinct count in the sample-tier statistics", () => {
    expect(out.distinctSample).toBeCloseTo(470.2, 0);
    expect(out.distinctExact).toBe(4_022);
  });

  it("sizes the pool as the code does: exact stats → source filter → sample (ADR 0033 D2)", () => {
    // The pool layer attaches a source-value store: the filter's cardinality sizes the pool, no exact tier needed.
    expect(out.poolDistinctVia).toBe("source filter");
    expect(out.poolTarget).toBe(512);
    expect(out.poolTargetSampleOnly).toBe(470);
    // No store: the sample distinct sizes it.
    const noStore = computeScenario({ ...preset90m, sourceFilter: false });
    expect(noStore.poolDistinctVia).toBe("sample");
    expect(noStore.poolTarget).toBe(470);
    expect(noStore.poolReuse).toBeCloseTo(90_000_000 / 470, 6);
    // Above the store's cap the filter reports unavailable.
    const huge = computeScenario({ ...preset90m, columnDistinct: CONSTANTS.sourceDomainCap + 1 });
    expect(huge.poolDistinctVia).toBe("sample");
    // The exact tier comes first.
    expect(computeScenario({ ...preset90m, tier: "exact", sourceFilter: false }).poolDistinctVia).toBe("exact stats");
    expect(poolDistinct({ tier: "exact", sourceFilter: false, columnDistinct: 94 }, 10)).toEqual({
      distinct: 94,
      via: "exact stats",
    });
  });

  it("the exact-tier recommendation is informational when the source filter sizes the pool, a warning when the sample does", () => {
    const rec = recommend(preset90m, out).find((r) => r.id === "exact-tier")!;
    expect(rec.tone).toBe("info");
    expect(rec.cite).toMatchObject({ path: "docs/adr/0033-pool-ladder-integrity-at-scale.md", line: 58 });
    const noStore = { ...preset90m, sourceFilter: false };
    expect(recommend(noStore, computeScenario(noStore)).find((r) => r.id === "exact-tier")?.tone).toBe("warn");
  });

  it("collides almost surely in a 10^12 keyspace; K must exceed M²/2", () => {
    expect(out.collisionProb).toBeCloseTo(1, 12);
    expect(out.expectedCollidingPairs).toBeCloseTo(4_050, 3);
    expect(out.keyspaceNeeded).toBeCloseTo(4.05e15, -10);
  });

  it("gates at 20 % in dev (18M blocker rows) and batches 90,000 rows per element", () => {
    expect(out.blockerRatio).toBe(0.2);
    expect(out.blockerRowsAllowed).toBe(18_000_000);
    expect(out.batchSize).toBe(90_000);
    expect(out.elements).toBe(1_000);
  });

  it("estimates time linearly from the measured R7 run of the default topology, with provenance", () => {
    expect(preset90m.sdkContainers).toBe("single");
    const t = out.time;
    expect(t.basis.label).toContain("R7 single");
    expect(t.basis.rows).toBe(20_000_000);
    expect(t.scale).toBe(4.5);
    expect(t.startupMin).toBe(13);
    expect(t.poolBranchMin).toBe(10.1);
    expect(t.generationMin).toBeCloseTo(163.8, 9);
    expect(t.dedupLoadMin).toBeCloseTo(64.35, 9);
    expect(t.totalMin).toBeCloseTo(251.25, 9);
    const multi = estimateTime({ ...preset90m, sdkContainers: "multi" });
    expect(multi.basis.label).toContain("R7 multi");
    expect(multi.totalMin).toBeCloseTo(12.4 + 12.9 + 17.1 * 4.5 + 6.4 * 4.5, 9);
    // The basis runs did not start full (ACCEPT_WORKERS_AT_4_MIN): the estimate says so.
    expect(t.basis.fleetRamp).toBe("1 → 4 @ +26 min");
    expect(multi.basis.fleetRamp).toBe("1 → 2 @ +29, 4 @ +37 min");
    expect(recommend(preset90m, out).find((r) => r.id === "fleet")?.why).toContain("1 → 4 @ +26 min");
  });

  it("warm runs drop the pool branch; streaming has no measured barrier", () => {
    expect(estimateTime({ ...preset90m, warm: true }).poolBranchMin).toBe(0);
    expect(estimateTime({ ...preset90m, uniqueness: "streaming" }).dedupLoadMin).toBeNull();
    expect(estimateTime({ ...preset90m, uniqueness: "exact_chained" }).basis.label).toContain("R6 cold");
    expect(estimateTime({ ...preset90m, sdkContainers: "multi" }).basis.label).toContain("R7 multi");
  });

  it("recommends the exact tier, the rare-category and tail fixes, and a wider keyspace — each cited", () => {
    const recs = recommend(preset90m, out);
    const ids = recs.map((r) => r.id);
    expect(ids).toEqual(
      expect.arrayContaining(["exact-tier", "tail", "pool-reuse", "keyspace", "sdk-multi", "fleet", "noise-floor"]),
    );
    // n = 10k ≥ 2,995 rows: a 0.1 % category is seen, so no rare-category warning.
    expect(ids).not.toContain("rare");
    for (const rec of recs) expect(rec.cite.path).toMatch(/\.(md|py|yml)$/);
  });

  it("the exact-tier preset drops the tier warning; the census preset has no sampling error", () => {
    const exact = presetById("exact-tier").inputs;
    expect(recommend(exact, computeScenario(exact)).map((r) => r.id)).not.toContain("exact-tier");
    const census = computeScenario(presetById("census").inputs);
    expect(census.census).toBe(true);
    expect(census.epsilon).toBe(0);
    expect(census.rareCapture).toBe(1);
  });
});
