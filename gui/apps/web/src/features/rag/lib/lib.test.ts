/**
 * The RAG tab's own maths and data plumbing: selection fallbacks and payload
 * caps, GReaT parsing, pool arithmetic from the code, seed metrics, the fast
 * PCA against the stats package's reference, k-NN overlap, colour slots
 * and screen-space picking.
 */
import { describe, expect, it } from "vitest";

import type { ChunkMeta, RagSet } from "@contracts/api";
import { knnPreservation, mulberry32, pca3, serializeGreat } from "@synthetic-platform/stats";

import { insidePolygon, nearestPoint } from "../explorer/usePicking";
import { categorise } from "./categories";
import { distinctColumnValues, learnColumns, naiveClauses, parseGreat, parseWithColumns } from "./greatParse";
import { clustersReached, hashingNoise, meanPairwiseCosine, seedMetrics } from "./metrics";
import { callBudget, idealRounds, inferPoolRoute, isBinaryClass, poolTarget, reuseAt } from "./pools";
import { jacobi3, knnKept, normalizeCoords, pca3Fast, projectPca, runPca, sphericalKMeans } from "./projection";
import { CHUNKS_MAX, resolveSelection, resolveSpace, spaceQueries, spaceRetrieves } from "./selection";
import { buildCloud } from "./useCloud";
import { maxAbsDiff, normRange, rowsOf, topKByCosine } from "./vectors";

function set(partial: Partial<RagSet>): RagSet {
  return {
    source_fqn: "demo.synthetic_source.users",
    reference_digest: "a".repeat(64),
    embedder_id: "hashing-384",
    embedder_version: "v1",
    row_docs: 1024,
    value_chunks: 2130,
    columns: ["city", "first_name"],
    dim: 384,
    created_at: "2026-09-02T11:10:00.000000Z",
    ...partial,
  };
}

const SETS: RagSet[] = [
  set({}),
  set({ embedder_id: "bge-small-en-v1.5" }),
  set({ reference_digest: "b".repeat(64), created_at: "2026-08-03T00:00:00.000000Z" }),
  set({ source_fqn: "demo.synthetic_source.orders", reference_digest: "c".repeat(64), value_chunks: 0, columns: [] }),
];

describe("selection", () => {
  it("defaults to the newest sample, the browser-embeddable embedder and the row documents", () => {
    const r = resolveSelection(SETS, {})!;
    expect(r.table).toBe("users");
    expect(r.set.reference_digest).toBe("a".repeat(64));
    expect(r.set.embedder_id).toBe("hashing-384");
    expect(r.space).toBe("rows");
    expect(r.notices).toEqual([]);
  });

  it("recovers from an unknown table, digest, embedder or space, and says so", () => {
    const r = resolveSelection(SETS, {
      table: "nope",
      digest: "zzz",
      embedder: "not-a-real-embedder",
      space: "values:missing",
    })!;
    expect(r.table).toBe("users");
    expect(r.set.embedder_id).toBe("hashing-384");
    expect(r.space).toBe("rows");
    expect(r.notices).toHaveLength(4);
  });

  it("keeps a valid space and refuses the ones the set cannot back", () => {
    expect(resolveSpace("values:city", SETS[0]!, SETS)).toBe("values:city");
    expect(resolveSpace("all", SETS[3]!, SETS)).toBe("rows");
    expect(resolveSpace("tables", SETS[0]!, SETS)).toBe("tables");
    expect(spaceRetrieves("rows")).toBe(true);
    expect(spaceRetrieves("values:city")).toBe(true);
    expect(spaceRetrieves("all")).toBe(false);
    expect(spaceRetrieves("tables")).toBe(false);
  });

  it("never asks the API for more than 3,000 vectors in one space", () => {
    for (const space of ["rows", "all", "tables", "values:city"] as const) {
      const queries = spaceQueries(space, SETS[0]!, SETS);
      const total = queries.reduce((a, q) => a + (q.limit ?? 0), 0);
      expect(total, space).toBeLessThanOrEqual(CHUNKS_MAX);
      for (const q of queries) expect(q.embedder).toMatch(/\/v1$/);
    }
    expect(spaceQueries("values:city", SETS[0]!, SETS)[0]).toMatchObject({ kind: "free_text_col", column: "city" });
  });
});

describe("GReaT parsing", () => {
  const columns = ["id", "notes", "city"];
  const row = { id: 7, notes: "fine, this is ok", city: "Pine" };
  const text = serializeGreat(row, columns);

  it("splits at the known column boundaries even when a value holds ', ' and ' is '", () => {
    expect(parseWithColumns(text, columns)).toEqual([
      { column: "id", value: "7" },
      { column: "notes", value: "fine, this is ok" },
      { column: "city", value: "Pine" },
    ]);
    // The naive split is fooled by the value; the learned sequence is not.
    expect(naiveClauses(text).map((c) => c.column)).toContain("this");
    expect(
      learnColumns([
        text,
        serializeGreat({ id: 8, notes: "ok", city: "Elm" }, columns),
        serializeGreat({ id: 9, notes: "x", city: "Oak" }, columns),
      ]),
    ).toEqual(columns);
  });

  it("re-serialising the parsed clauses gives the stored sentence back, byte for byte", () => {
    const clauses = parseGreat(text, columns);
    expect(serializeGreat(Object.fromEntries(clauses.map((c) => [c.column, c.value])), columns)).toBe(text);
  });

  it("collects a column's first-seen distinct values, skipping null (rung 2)", () => {
    const texts = [
      serializeGreat({ id: 1, notes: "a", city: "Pine" }, columns),
      serializeGreat({ id: 2, notes: "b", city: null }, columns),
      serializeGreat({ id: 3, notes: "c", city: "Elm" }, columns),
      serializeGreat({ id: 4, notes: "d", city: "Pine" }, columns),
    ];
    expect(distinctColumnValues(texts, columns, "city")).toEqual(["Pine", "Elm"]);
  });
});

describe("pools, from the code", () => {
  it("sizes the target as min(num_rows, distinct, 512), skipping unknown bounds", () => {
    expect(poolTarget(10_000_000, 4022, 512)).toBe(512);
    expect(poolTarget(10_000_000, 94, 512)).toBe(94);
    expect(poolTarget(100, 4022, 512)).toBe(100);
    expect(poolTarget(0, 0, 512)).toBe(512);
    expect(poolTarget(0, 0, 0)).toBe(1);
  });

  it("budgets max(3, 2·⌈target / 128⌉) calls", () => {
    expect(callBudget(512)).toBe(8);
    expect(callBudget(368)).toBe(6);
    expect(callBudget(40)).toBe(3);
    expect(idealRounds(512)).toBe(4);
  });

  it("recognises binary-class pools as is_binary_class does", () => {
    expect(isBinaryClass(["\u0001ab", "\u0002cd", "ok"])).toBe(true);
    expect(isBinaryClass(["a\tb", "c\nd"])).toBe(false);
    expect(isBinaryClass(["\u0001"])).toBe(false);
    expect(isBinaryClass(["\u0085x", "y", "z", "\u009fw"])).toBe(true);
  });

  it("infers the route from the row", () => {
    expect(inferPoolRoute(null)).toBe("no_pool");
    expect(inferPoolRoute({ values: ["a", "b"], attempts: 4 })).toBe("llm_ladder");
    expect(inferPoolRoute({ values: ["\u0001a", "\u0002b"], attempts: 0 })).toBe("binary_fallback");
    expect(inferPoolRoute({ values: ["a", "b"], attempts: 0 })).toBe("no_rounds");
  });

  it("computes reuse at scale", () => {
    expect(reuseAt(10_000_000, 512)).toBeCloseTo(19531.25, 6);
    expect(reuseAt(10, 0)).toBeNull();
  });
});

describe("seed metrics", () => {
  const rows = rowsOf(Float64Array.from([1, 0, 0, 1, 0.6, 0.8, -1, 0]), 2);

  it("measures coverage, diversity and redundancy with cosine distance", () => {
    const m = seedMetrics(rows, [0, 1]);
    // Items: e0 (0), e1 (0), (0.6, 0.8) → nearest e1 at 1 − 0.8 = 0.2, −e0 → nearest e1 at 1 − 0 = 1.
    expect(m.coverage).toBeCloseTo((0 + 0 + 0.2 + 1) / 4, 12);
    expect(m.radius).toBeCloseTo(1, 12);
    expect(m.diversity).toBeCloseTo(1, 12);
    expect(m.redundancy).toBeCloseTo(0, 12);
    expect(seedMetrics(rows, [0]).diversity).toBeNull();
  });

  it("averages pairwise cosine exactly for small selections", () => {
    const r = meanPairwiseCosine(rows, [0, 1, 2])!;
    expect(r.exact).toBe(true);
    expect(r.mean).toBeCloseTo((0 + 0.6 + 0.8) / 3, 12);
    expect(meanPairwiseCosine(rows, [0])).toBeNull();
  });

  it("counts clusters reached and gives the hashing noise band", () => {
    expect(clustersReached([0, 1, 2], Int32Array.from([0, 0, 1, 2]))).toBe(2);
    expect(clustersReached([0], null)).toBeNull();
    expect(hashingNoise(384)).toBeCloseTo(0.051, 3);
  });

  it("ranks by cosine in the exact index's order (ties to the lower index)", () => {
    const hits = topKByCosine([1, 0], rowsOf(Float64Array.from([1, 0, 1, 0, 0, 1]), 2), 2);
    expect(hits.map((h) => h.index)).toEqual([0, 1]);
    expect(normRange(rows)).toEqual({ min: 1, max: 1 });
    expect(maxAbsDiff([1, 2], [1, 2.5])).toBe(0.5);
  });
});

function clusteredData(n: number, dim: number, seed = 3): Float32Array {
  const rand = mulberry32(seed);
  const centres = [0, 1, 2].map(() => Array.from({ length: dim }, () => rand() - 0.5));
  const out = new Float32Array(n * dim);
  for (let i = 0; i < n; i += 1) {
    const c = centres[i % 3]!;
    let s = 0;
    for (let j = 0; j < dim; j += 1) {
      const v = c[j]! * 3 + (rand() - 0.5) * 0.4;
      out[i * dim + j] = v;
      s += v * v;
    }
    for (let j = 0; j < dim; j += 1) out[i * dim + j]! /= Math.sqrt(s);
  }
  return out;
}

describe("projection", () => {
  const dim = 48;
  const data = clusteredData(240, dim);

  it("diagonalises a symmetric 3 × 3 matrix", () => {
    const { values, vectors } = jacobi3([
      [4, 1, 0],
      [1, 3, 1],
      [0, 1, 2],
    ]);
    const a = [
      [4, 1, 0],
      [1, 3, 1],
      [0, 1, 2],
    ];
    for (let c = 0; c < 3; c += 1) {
      const v = [vectors[0]![c]!, vectors[1]![c]!, vectors[2]![c]!];
      const av = a.map((row) => row.reduce((s, x, j) => s + x * v[j]!, 0));
      av.forEach((x, j) => expect(x).toBeCloseTo(values[c]! * v[j]!, 8));
    }
    expect(values.reduce((s, x) => s + x, 0)).toBeCloseTo(9, 10);
  });

  it("finds the same top-3 variance as the stats package's PCA", () => {
    const fast = pca3Fast(data, dim);
    const ref = pca3(data, dim, { iterations: 80 });
    fast.explained.forEach((e, i) => expect(e).toBeCloseTo(ref.explained[i]!, 3));
    // Components are unit and orthogonal.
    for (let a = 0; a < 3; a += 1)
      for (let b = 0; b < 3; b += 1) {
        let d = 0;
        for (let j = 0; j < dim; j += 1) d += fast.components[a]![j]! * fast.components[b]![j]!;
        expect(d).toBeCloseTo(a === b ? 1 : 0, 6);
      }
  });

  it("projects a new vector exactly where PCA put the same vector", () => {
    const result = runPca(data, dim);
    const p = projectPca(data.subarray(5 * dim, 6 * dim), result.pca!, result.frame);
    for (let c = 0; c < 3; c += 1) expect(p[c]).toBeCloseTo(result.coords[5 * 3 + c]!, 4);
  });

  it("normalises to a unit 98th-percentile radius around the centre", () => {
    const { coords, frame } = normalizeCoords(Float32Array.from([0, 0, 0, 2, 0, 0, 4, 0, 0]));
    expect(frame.center).toEqual([2, 0, 0]);
    expect(Math.max(...Array.from(coords).map(Math.abs))).toBeCloseTo(1, 6);
  });

  it("measures k-NN overlap like the stats package's knnPreservation", () => {
    const { coords } = runPca(data, dim);
    const mine = knnKept(data, dim, coords, 10, 60)!;
    const ref = knnPreservation(data, dim, coords, 3, 10, 60);
    expect(mine).toBeCloseTo(ref, 1);
    expect(mine).toBeGreaterThan(0);
    expect(mine).toBeLessThanOrEqual(1);
  });

  it("recovers well-separated clusters", () => {
    const labels = sphericalKMeans(data, dim);
    for (let i = 0; i < 240; i += 3) {
      expect(labels[i + 1]).not.toBe(labels[i]);
      expect(labels[i]).toBe(labels[0]);
    }
  });
});

describe("categories and picking", () => {
  const meta: ChunkMeta[] = ["row_doc", "free_text_col", "free_text_col", "free_text_col", "free_text_col"].map(
    (kind, i) => ({
      chunk_id: `c${i}`,
      source_fqn: "demo.synthetic_source.users",
      chunk_kind: kind as ChunkMeta["chunk_kind"],
      chunk_index: 0,
      chunk_text: `t${i}`,
      column: kind === "row_doc" ? null : ["city", "email", "first_name", "last_name"][i - 1]!,
      row_digest: `r${i}`,
      embedder_id: "hashing-384",
      embedder_version: "v1",
    }),
  );
  const cloud = buildCloud("k", "all", [{ meta, vectors: new Float32Array(5 * 2).fill(0.5), dim: 2 }]);

  it("keeps three categorical slots in fixed order and folds the rest into Other", () => {
    const { categories, of } = categorise(cloud, "column", { columns: ["city", "email", "first_name", "last_name"] });
    expect(categories.map((c) => [c.label, c.slot])).toEqual([
      ["Row document", 0],
      ["city", 1],
      ["email", 2],
      ["first_name", null],
      ["last_name", null],
    ]);
    expect(Array.from(of)).toEqual([0, 1, 2, 3, 4]);
  });

  it("picks the nearest point within the hit radius, and tests lasso membership", () => {
    const project = (i: number) =>
      [
        [10, 10],
        [100, 100],
        [30, 12],
      ][i] as [number, number];
    expect(nearestPoint(3, project, 24, 12)).toBe(2);
    expect(nearestPoint(3, project, 300, 300)).toBeNull();
    const square: [number, number][] = [
      [0, 0],
      [50, 0],
      [50, 50],
      [0, 50],
    ];
    expect(insidePolygon(10, 10, square)).toBe(true);
    expect(insidePolygon(100, 100, square)).toBe(false);
  });
});
