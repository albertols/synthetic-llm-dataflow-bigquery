/**
 * The RAG tab's retrieval path against the pipeline's goldens
 * (`generated/golden/retrieval.json`, written by export_golden_fixtures.py
 * from `sdfb_core.rag.retrieval`): the picks the tab shows — through its own
 * adapters (flat row-major buffers → row views → `runStrategy`, and the
 * worker's `computeStrategies`) — are exactly the pipeline's, for every
 * strategy, k and attempt in the fixture, duplicates and a collapsed matrix
 * included. The teaching contrasts are labelled and never claim the port.
 */
import { describe, expect, it } from "vitest";

import golden from "@contracts/generated/golden/retrieval.json";
import { centroidTopK, goldenMatrix, kcenter, TEACHING_ONLY } from "@synthetic-platform/stats";

import {
  EXACT_LABEL,
  PIPELINE_STRATEGIES,
  runStrategy,
  STRATEGIES,
  STRATEGY_IDS,
  strategyWalk,
  TEACHING_LABEL,
} from "./lib/strategies";
import { computeStrategies } from "./lib/strategyJob";
import { rowsOf } from "./lib/vectors";

type GoldenCase = { matrix: string; strategy: string; k: number; attempt: number; picks: number[] };

/** A golden matrix as the tab holds vectors: one flat row-major buffer, rows as views. */
function flat(name: string): { data: Float64Array; dim: number } {
  const spec = (golden.matrices as Record<string, Parameters<typeof goldenMatrix>[0]>)[name]!;
  const m = goldenMatrix(spec);
  const dim = m[0]!.length;
  const data = new Float64Array(m.length * dim);
  m.forEach((row, i) => data.set(row, i * dim));
  return { data, dim };
}

const cases = golden.cases as GoldenCase[];

describe("retrieval, as the RAG tab runs it", () => {
  it("covers the three pipeline strategies with enough cases", () => {
    expect(cases.length).toBeGreaterThan(40);
    expect(new Set(cases.map((c) => c.strategy))).toEqual(new Set(PIPELINE_STRATEGIES));
  });

  it("runStrategy over row views picks exactly the golden items", () => {
    const failures: string[] = [];
    for (const c of cases) {
      const { data, dim } = flat(c.matrix);
      const rows = rowsOf(data, dim);
      const picks = runStrategy(c.strategy as (typeof PIPELINE_STRATEGIES)[number], rows, c.k, { attempt: c.attempt });
      // select_seed_examples returns every item, in order, when n ≤ k (the goldens rank them: the pure index functions).
      const expected = c.k > 0 && rows.length <= c.k ? rows.map((_, i) => i) : c.picks;
      if (JSON.stringify(picks) !== JSON.stringify(expected))
        failures.push(
          `${c.matrix} ${c.strategy} k=${c.k} attempt=${c.attempt}: ${picks.join(",")} ≠ ${c.picks.join(",")}`,
        );
    }
    expect(failures).toEqual([]);
  });

  it("the worker job (computeStrategies) returns the golden picks and a walk that is the k-center port", () => {
    const { data, dim } = flat("main");
    const f32 = Float32Array.from(data);
    // The worker receives Float32 vectors: the picks must still match the golden on this fixture.
    for (const k of [1, 2, 4, 8, 16]) {
      for (const attempt of [0, 1, 2]) {
        const job = computeStrategies(f32, dim, k, attempt, "kcenter_rotate");
        for (const id of PIPELINE_STRATEGIES) {
          const expected = cases.find(
            (c) =>
              c.matrix === "main" &&
              c.strategy === id &&
              c.k === k &&
              (id !== "kcenter_rotate" || c.attempt === attempt),
          );
          if (!expected) continue;
          expect(job.rows.find((r) => r.id === id)!.picks, `${id} k=${k} attempt=${attempt}`).toEqual(expected.picks);
        }
        expect(job.walk.steps.map((s) => s.pick)).toEqual(job.rows.find((r) => r.id === "kcenter_rotate")!.picks);
      }
    }
    const plain = computeStrategies(f32, dim, 8, 0, "kcenter");
    expect(plain.walk.steps.map((s) => s.pick)).toEqual(kcenter(rowsOf(f32, dim), 8));
  });

  it("the walk's frontier is the squared distance to the nearest pick so far, non-increasing step by step", () => {
    const { data, dim } = flat("main");
    const f32 = Float32Array.from(data);
    const n = f32.length / dim;
    const { walk } = computeStrategies(f32, dim, 6, 0, "kcenter");
    expect(walk.frontier).toHaveLength(6 * n);
    for (let t = 1; t < walk.steps.length; t += 1) {
      for (let i = 0; i < n; i += 1)
        expect(walk.frontier[t * n + i]!).toBeLessThanOrEqual(walk.frontier[(t - 1) * n + i]!);
      // A picked item is at distance 0 from the picks.
      expect(walk.frontier[t * n + walk.steps[t]!.pick]).toBe(0);
      // Each pick was the farthest item before it was picked.
      const before = walk.frontier.subarray((t - 1) * n, t * n);
      expect(before[walk.steps[t]!.pick]).toBe(Math.max(...before));
    }
    expect(strategyWalk("kcenter", rowsOf(f32, dim), 6).map((s) => s.pick)).toEqual(walk.steps.map((s) => s.pick));
  });

  it("labels the pipeline strategies as exact ports and the teaching ones as not in the pipeline", () => {
    for (const id of STRATEGY_IDS) {
      const info = STRATEGIES[id];
      const pipeline = (PIPELINE_STRATEGIES as readonly string[]).includes(id);
      expect(info.inPipeline).toBe(pipeline);
      expect(info.truth).toBe(pipeline ? EXACT_LABEL : TEACHING_LABEL);
      expect(Boolean(info.flag)).toBe(pipeline);
    }
    expect(TEACHING_LABEL).toBe("Teaching contrast — not in pipeline");
    // The stats package's teaching-only list is the tab's two contrasts.
    expect([...TEACHING_ONLY]).toEqual(["mmr", "randomPick"]);
    expect(STRATEGY_IDS.filter((id) => !STRATEGIES[id].inPipeline)).toEqual(["random", "mmr"]);
  });

  it("below n the tab's centroid path is the golden ranked index (retrieve_centroid_top_k)", () => {
    for (const c of cases.filter((x) => x.strategy === "centroid" && x.k > 0)) {
      const { data, dim } = flat(c.matrix);
      const rows = rowsOf(data, dim);
      if (c.k < rows.length) expect(runStrategy("centroid", rows, c.k)).toEqual(c.picks);
      else expect(centroidTopK(rows, c.k)).toEqual(c.picks);
    }
  });

  it("n ≤ k returns every item in order, as select_seed_examples does", () => {
    const { data, dim } = flat("collapsed");
    const rows = rowsOf(data, dim);
    for (const id of PIPELINE_STRATEGIES) expect(runStrategy(id, rows, rows.length + 3)).toEqual(rows.map((_, i) => i));
    expect(runStrategy("centroid", [], 8)).toEqual([]);
  });
});
