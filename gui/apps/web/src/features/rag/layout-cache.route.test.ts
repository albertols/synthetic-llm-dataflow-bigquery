/**
 * The UMAP layout cache through Fastify `inject`: the route plugin
 * (apps/server/src/routes/rag.extra.ts) mounted as app.ts mounts it — under
 * /api/x/rag, with the BFF's body limit (read from app.ts's source, so the
 * test follows it) and its BadRequest → 400 handler. A realistic
 * 3,000-point layout, encoded exactly as the browser encodes it
 * (layoutCache.ts), is stored and read back bit for bit; the JSON-array form
 * it replaced is shown to overflow the limit; malformed layouts are refused.
 *
 * It lives in the RAG feature folder because the route file is the only
 * server file the tab owns. It cannot import app.ts itself: config.ts builds
 * a `new URL(…, import.meta.url)` default that the web project's Vite
 * transform turns into an http URL.
 */
import Fastify, { type FastifyInstance } from "fastify";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { mulberry32 } from "@synthetic-platform/stats";

import appSource from "../../../../server/src/app.ts?raw";
import { BadRequest, type RouteOptions } from "../../../../server/src/routes/context";
import ragExtra from "../../../../server/src/routes/rag.extra";
import { float32ToBase64 } from "./lib/float32Base64";
import { layoutCacheBody, LAYOUT_CACHE_PATH, parseCachedLayout, projectionUrl } from "./lib/layoutCache";

/** `bodyLimit: 64 * 1024` in buildApp. */
const BODY_LIMIT = (() => {
  const match = /bodyLimit:\s*(\d+)\s*\*\s*(\d+)/.exec(appSource);
  if (!match) throw new Error("app.ts no longer sets bodyLimit as a product; update this test");
  return Number(match[1]) * Number(match[2]);
})();
const N = 3000;
const HOST = { host: "127.0.0.1:8787" };

let app: FastifyInstance;

beforeAll(async () => {
  app = Fastify({ logger: false, bodyLimit: BODY_LIMIT });
  app.setErrorHandler((error, _request, reply) =>
    error instanceof BadRequest
      ? reply.code(400).send({ statusCode: 400, error: "Bad Request", message: error.message, details: error.issues })
      : reply.send(error),
  );
  // The provider and config are never touched by the projection routes; only the cache's get/set are.
  // (A Map stands in for the app's LRUCache, whose browser build the web test project resolves.)
  const options = { provider: {}, config: {}, cache: new Map<string, object>() } as unknown as RouteOptions;
  await app.register(ragExtra, { ...options, prefix: "/api/x/rag" });
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
  it("reads the BFF's body limit from app.ts", () => {
    expect(BODY_LIMIT).toBe(64 * 1024);
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
