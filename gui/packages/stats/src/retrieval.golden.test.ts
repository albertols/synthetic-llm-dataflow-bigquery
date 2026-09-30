/**
 * centroid top-k, greedy k-center and k-center-rotate must pick exactly what
 * `sdfb_core.rag.retrieval` picks (the pure-Python exact index), including
 * tie-breaks on duplicate rows and the never-re-pick rule on a collapsed matrix.
 */
import { describe, expect, it } from "vitest";

import golden from "@contracts/generated/golden/retrieval.json";

import { centroid, centroidTopK, kcenter, kcenterRotate, selectSeedExamples } from "./retrieval";
import { goldenMatrix, mulberry32U32 } from "./rng";

const matrices = Object.fromEntries(
  Object.entries(golden.matrices).map(([name, spec]) => [name, goldenMatrix(spec)]),
) as Record<string, number[][]>;

describe("retrieval ports", () => {
  it("rebuild the golden matrices bit for bit (mulberry32)", () => {
    const main = matrices.main!;
    expect(main).toHaveLength(64);
    expect(main[0]).toHaveLength(384);
    for (const [name, check] of Object.entries(golden.checks)) {
      const m = matrices[name]!;
      const sum = m.reduce((acc, row) => acc + row.reduce((a, b) => a + b, 0), 0);
      expect(sum).toBeCloseTo(check.sum, 9);
      expect(m[0]!.slice(0, 4).map((v) => Number(v.toFixed(12)))).toEqual(check.first);
      expect(
        centroid(m)
          .slice(0, 4)
          .map((v) => Number(v.toFixed(12))),
      ).toEqual(check.centroid_head);
    }
    const draws = mulberry32U32(1);
    // The canonical mulberry32 (the JS original) and the Python port agree.
    expect([draws(), draws(), draws()]).toEqual([2693262067, 11749833, 2265367787]);
  });

  it("pick exactly the golden items for every strategy, k and attempt", () => {
    expect(golden.cases.length).toBeGreaterThan(40);
    for (const testCase of golden.cases) {
      const vectors = matrices[testCase.matrix]!;
      let picks: number[];
      if (testCase.strategy === "centroid") picks = centroidTopK(vectors, testCase.k);
      else if (testCase.strategy === "kcenter") picks = kcenter(vectors, testCase.k);
      else picks = kcenterRotate(vectors, testCase.k, testCase.attempt);
      expect(picks, `${testCase.matrix} ${testCase.strategy} k=${testCase.k} attempt=${testCase.attempt}`).toEqual(
        testCase.picks,
      );
    }
  });

  it("select_seed_examples dispatches the same way", () => {
    const vectors = matrices.main!;
    for (const testCase of golden.cases.filter((c) => c.matrix === "main" && c.k > 0 && c.k < 64)) {
      const strategy = testCase.strategy as "centroid" | "kcenter" | "kcenter_rotate";
      expect(selectSeedExamples(vectors, testCase.k, strategy, testCase.attempt)).toEqual(testCase.picks);
    }
    expect(selectSeedExamples(vectors, 0, "kcenter", 0)).toEqual([]);
    expect(selectSeedExamples([], 8, "centroid", 0)).toEqual([]);
  });
});
