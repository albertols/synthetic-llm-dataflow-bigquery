/**
 * The TS HashingEmbedder must reproduce `sdfb_core.rag.embedding.HashingEmbedder`
 * bit for bit on the integer part (tokens, uint64-modulo buckets, signs, counts)
 * and to 1e-6 on the normalized values. Goldens: scripts/gui/export_golden_fixtures.py.
 */
import { createHash } from "node:crypto";

import { describe, expect, it } from "vitest";

import golden from "@contracts/generated/golden/hashing_embedder.json";

import { hashingBucket, hashingEmbed, hashingTokens, PY_WHITESPACE, pySplit } from "./hashing";
import { sha256 } from "./sha256";

type Case = (typeof golden.cases)[number];

function dense(testCase: Case, dim: number): number[] {
  const out = new Array<number>(dim).fill(0);
  for (const [index, value] of testCase.nonzero) out[index as number] = value as number;
  return out;
}

describe("sha256", () => {
  it("matches node:crypto on text, empty input and multi-block input", () => {
    for (const text of ["", "abc", "0\u0000hello", "x".repeat(1000), "émoji 🎁"]) {
      const bytes = new TextEncoder().encode(text);
      expect(Buffer.from(sha256(bytes)).toString("hex")).toBe(createHash("sha256").update(bytes).digest("hex"));
    }
  });
});

describe("HashingEmbedder port", () => {
  it("uses exactly the whitespace set of Python's str.split()", () => {
    expect([...PY_WHITESPACE].sort((a, b) => a - b)).toEqual(golden.whitespace);
  });

  it("splits every golden text into the same tokens", () => {
    expect(golden.cases).toHaveLength(40);
    for (const testCase of golden.cases) {
      expect(hashingTokens(testCase.text), JSON.stringify(testCase.text)).toEqual(testCase.tokens);
    }
    expect(pySplit("a\u001fb\u0085c\ufeffd\u200be")).toEqual(["a", "b", "c\ufeffd\u200be"]);
    expect(pySplit("   ")).toEqual([]);
  });

  it("hashes every token to the same bucket and sign (uint64 modulo)", () => {
    for (const testCase of golden.cases) {
      for (const [token, bucket, sign] of testCase.buckets) {
        expect(hashingBucket(token as string, golden.dim, golden.seed)).toEqual({ bucket, sign });
      }
    }
    for (const testCase of golden.small.cases) {
      for (const [token, bucket, sign] of testCase.buckets) {
        expect(hashingBucket(token as string, golden.small.dim, golden.small.seed)).toEqual({ bucket, sign });
      }
    }
  });

  it("embeds every text to the golden vector within 1e-6 (the zero-vector case included)", () => {
    for (const testCase of golden.cases) {
      const vector = hashingEmbed(testCase.text, { dim: golden.dim, seed: golden.seed });
      const expected = dense(testCase, golden.dim);
      const maxError = Math.max(...Array.from(vector, (v, i) => Math.abs(v - expected[i]!)));
      expect(maxError, JSON.stringify(testCase.text)).toBeLessThan(1e-6);
    }
    for (const testCase of golden.small.cases) {
      const vector = hashingEmbed(testCase.text, { dim: golden.small.dim, seed: golden.small.seed });
      const expected = dense(testCase, golden.small.dim);
      expect(Math.max(...Array.from(vector, (v, i) => Math.abs(v - expected[i]!)))).toBeLessThan(1e-6);
    }
  });

  it("returns unit vectors", () => {
    for (const testCase of golden.cases) {
      const vector = hashingEmbed(testCase.text);
      expect(Math.hypot(...vector)).toBeCloseTo(1, 12);
    }
  });
});
