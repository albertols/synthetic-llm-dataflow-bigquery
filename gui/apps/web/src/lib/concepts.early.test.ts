/**
 * `getConcept` before the lazy concept files have merged: a fresh registry
 * (its own module instance), so the other suites' `loadConcepts()` does not hide the miss.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => vi.restoreAllMocks());

describe("getConcept before the lazy files load", () => {
  it("finds core concepts at once; a tab concept misses, warns once, and starts the load", async () => {
    vi.resetModules();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { getConcept, conceptsComplete } = await import("./concepts");

    expect(getConcept("core:noise-floor")?.title).toBe("Noise floor");
    expect(conceptsComplete()).toBe(false);
    expect(warn).not.toHaveBeenCalled();

    expect(getConcept("rag:great")).toBeUndefined();
    expect(getConcept("rag:great")).toBeUndefined();
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('getConcept("rag:great")'));

    // The miss started the load: the same call succeeds once the files have merged.
    await vi.waitFor(() => expect(conceptsComplete()).toBe(true), { timeout: 10_000 });
    expect(getConcept("rag:great")?.id).toBe("rag:great");
    // Once complete, an unknown id is simply unknown (no warning).
    expect(getConcept("rag:not-a-concept")).toBeUndefined();
    expect(warn).toHaveBeenCalledTimes(1);
  }, 15_000);
});
