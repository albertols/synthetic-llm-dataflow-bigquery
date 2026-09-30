import { afterEach, describe, expect, it, vi } from "vitest";

import { readCache } from "./layoutCacheIo";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("readCache", () => {
  it("treats a failed read as a miss and says so on the console in development", async () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    vi.stubGlobal("fetch", () => Promise.resolve(new Response("{}", { status: 503 })));
    expect(await readCache("/api/x/rag/projection?x", 10)).toBeNull();
    expect(info).toHaveBeenCalledWith(expect.stringContaining("answered 503"));

    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("Failed to fetch")));
    expect(await readCache("/api/x/rag/projection?x", 10)).toBeNull();
    expect(info).toHaveBeenLastCalledWith(expect.stringContaining("Failed to fetch"));
  });

  it("a plain miss is not a failure", async () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ hit: false })));
    expect(await readCache("/api/x/rag/projection?x", 10)).toBeNull();
    expect(info).not.toHaveBeenCalled();
  });
});
