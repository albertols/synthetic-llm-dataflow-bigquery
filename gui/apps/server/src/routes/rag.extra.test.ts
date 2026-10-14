/**
 * The UMAP layout cache (RAG's routes/rag.extra.ts) through the real BFF:
 * `buildApp` mounts it under /api/x/rag with the app's body limit and its
 * BadRequest → 400 handler. A realistic 3,000-point layout, encoded exactly as
 * the browser encodes it (features/rag/lib/layoutCache.ts), is stored and read
 * back bit for bit; the JSON-array form it replaced is shown to overflow the
 * body limit; malformed layouts are refused.
 */
import type { FastifyInstance } from "fastify";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { mulberry32 } from "@synthetic-platform/stats";

import { float32ToBase64 } from "../../../web/src/features/rag/lib/float32Base64";
import {
  layoutCacheBody,
  LAYOUT_CACHE_PATH,
  parseCachedLayout,
  projectionUrl,
} from "../../../web/src/features/rag/lib/layoutCache";
import { buildApp } from "../app";
import { loadConfig } from "../config";
import type { DataProvider } from "../providers/types";

/** buildApp's `bodyLimit`. */
const BODY_LIMIT = 64 * 1024;
const N = 3000;
const HOST = { host: "127.0.0.1:8787" };

let app: FastifyInstance;

beforeAll(async () => {
  // The projection routes never touch the provider; only the app's LRU cache.
  const provider = { mode: "mock" } as unknown as DataProvider;
  app = await buildApp({ config: loadConfig({ LOG_LEVEL: "silent" }), provider, serveStatic: false });
  await app.ready();
});

afterAll(async () => {
  await app.close();
});

/** A UMAP-like layout: n × 3 Float32 in about ±2.5 (normalised, outliers beyond 1). */
function layout(n: number, seed = 42) {
  const rand = mulberry32(seed);
  const coords = new Float32Array(n * 3);
  for (let i = 0; i < coords.length; i += 1) coords[i] = (rand() - 0.5) * 5;
  return { coords, frame: { center: [0.1, -0.2, 0.3] as [number, number, number], scale: 0.87 } };
}

function key(n: number, tag: string) {
  return { space: "all" as const, idsHash: (tag.repeat(64) + "0".repeat(64)).slice(0, 64), n };
}

const DIGEST = "c56bf2985de78330b2fcb76879f4abe7f2cdb919ffb4cf199c33a6368c02fb98";
const EMBEDDER = "hashing-384/v1";

describe("the RAG layout cache through the BFF", () => {
  it("the app's body limit is 64 KiB: just under reaches the route (400), just over is a 413", async () => {
    const post = (bytes: number) =>
      app.inject({
        method: "POST",
        url: LAYOUT_CACHE_PATH,
        headers: { ...HOST, "content-type": "application/json" },
        payload: JSON.stringify({ pad: "x".repeat(bytes - '{"pad":""}'.length) }),
      });
    expect((await post(BODY_LIMIT)).statusCode).toBe(400);
    expect((await post(BODY_LIMIT + 1)).statusCode).toBe(413);
  });

  it("stores a 3,000-point layout inside the 64 KB body limit and reads it back bit for bit", async () => {
    const cloud = key(N, "a");
    const { coords, frame } = layout(N);
    const body = JSON.stringify(layoutCacheBody(cloud, DIGEST, EMBEDDER, { coords, frame }, 0.31));
    expect(body.length).toBeLessThan(BODY_LIMIT);
    expect(body.length).toBeGreaterThan(48_000);

    const miss = await app.inject({ method: "GET", url: projectionUrl(cloud, DIGEST, EMBEDDER), headers: HOST });
    expect(miss.statusCode).toBe(200);
    expect(miss.json()).toEqual({ hit: false });

    const stored = await app.inject({
      method: "POST",
      url: LAYOUT_CACHE_PATH,
      headers: { ...HOST, "content-type": "application/json" },
      payload: body,
    });
    expect(stored.statusCode).toBe(201);

    const hit = await app.inject({ method: "GET", url: projectionUrl(cloud, DIGEST, EMBEDDER), headers: HOST });
    expect(hit.statusCode).toBe(200);
    const parsed = parseCachedLayout(hit.json(), N);
    expect(parsed).not.toBeNull();
    expect(parsed!.trust).toBe(0.31);
    expect(parsed!.result.frame).toEqual(frame);
    // Float32 in, Float32 out: the same bits, so exact equality (float32 precision by construction).
    expect(parsed!.result.coords).toHaveLength(N * 3);
    expect(Array.from(parsed!.result.coords)).toEqual(Array.from(coords));
    // A reader expecting another point count treats it as a miss.
    expect(parseCachedLayout(hit.json(), N - 1)).toBeNull();
  });

  it("would not fit as the JSON number array it replaced (413 from the body limit)", async () => {
    const cloud = key(N, "b");
    const { coords, frame } = layout(N, 7);
    const old = JSON.stringify({
      ...layoutCacheBody(cloud, DIGEST, EMBEDDER, { coords, frame }, null),
      coords: Array.from(coords, (v) => Math.round(v * 1e5) / 1e5),
    });
    expect(old.length).toBeGreaterThan(BODY_LIMIT);
    const response = await app.inject({
      method: "POST",
      url: LAYOUT_CACHE_PATH,
      headers: { ...HOST, "content-type": "application/json" },
      payload: old,
    });
    expect(response.statusCode).toBe(413);
  });

  it("refuses a layout whose bytes do not match n, a non-finite value, or text that is not base64", async () => {
    const post = (payload: object) =>
      app.inject({
        method: "POST",
        url: LAYOUT_CACHE_PATH,
        headers: { ...HOST, "content-type": "application/json" },
        payload: JSON.stringify(payload),
      });
    const cloud = key(10, "c");
    const { coords, frame } = layout(10);
    const body = layoutCacheBody(cloud, DIGEST, EMBEDDER, { coords, frame }, null);

    const short = await post({ ...body, coords: float32ToBase64(coords.subarray(0, 27)) });
    expect(short.statusCode).toBe(400);
    expect(short.json<{ message: string }>().message).toMatch(/120 bytes\), got 108 bytes/);

    const bad = Float32Array.from(coords);
    bad[5] = Number.NaN;
    const nan = await post({ ...body, coords: float32ToBase64(bad) });
    expect(nan.statusCode).toBe(400);
    expect(nan.json<{ message: string }>().message).toMatch(/coords\[5\]/);

    const huge = Float32Array.from(coords);
    huge[0] = 5e4;
    expect((await post({ ...body, coords: float32ToBase64(huge) })).statusCode).toBe(400);

    expect((await post({ ...body, coords: "not base64!" })).statusCode).toBe(400);
    expect((await post({ ...body, n: 3001 })).statusCode).toBe(400);
    const miss = await app.inject({ method: "GET", url: projectionUrl(cloud, DIGEST, EMBEDDER), headers: HOST });
    expect(miss.json()).toEqual({ hit: false });
  });
});
