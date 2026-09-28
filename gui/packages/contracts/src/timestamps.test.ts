import { describe, expect, it } from "vitest";

import { evaluationFilterSchema } from "./api";
import { canonicalBound, canonicalTimestamp } from "./timestamps";

describe("canonicalBound (filter bounds sent to TIMESTAMP(@from) / TIMESTAMP(@to))", () => {
  it("keeps six digits, fills missing ones, and turns dates and offsets into UTC", () => {
    expect(canonicalBound("2026-09-01")).toBe("2026-09-01T00:00:00.000000Z");
    expect(canonicalBound("2026-09-01T10:00:00Z")).toBe("2026-09-01T10:00:00.000000Z");
    expect(canonicalBound("2026-09-01T10:00:00.123456Z")).toBe("2026-09-01T10:00:00.123456Z");
    expect(canonicalBound("2026-09-01T12:00:00.5+02:00")).toBe("2026-09-01T10:00:00.500000Z");
    expect(canonicalBound("not a time")).toBeNull();
  });

  it("rounds extra digits up, so `at >= from` and `at < to` do not change on microsecond data", () => {
    expect(canonicalBound("2026-09-01T10:00:00.1234560Z")).toBe("2026-09-01T10:00:00.123456Z");
    expect(canonicalBound("2026-09-01T10:00:00.1234561Z")).toBe("2026-09-01T10:00:00.123457Z");
    expect(canonicalBound("2026-12-31T23:59:59.9999999Z")).toBe("2027-01-01T00:00:00.000000Z");
    // A stored microsecond value just below the nanosecond bound is excluded either way.
    const bound = "2026-09-01T10:00:00.123456001Z";
    const stored = "2026-09-01T10:00:00.123456Z";
    expect(stored >= canonicalBound(bound)!).toBe(false);
    expect(canonicalTimestamp(bound)).toBe(stored);
  });

  it("is what the API contract does to `from` / `to`", () => {
    const parsed = evaluationFilterSchema.parse({ from: "2026-09-01T10:00:00.123456789Z", to: "2026-09-02" });
    expect(parsed.from).toBe("2026-09-01T10:00:00.123457Z");
    expect(parsed.to).toBe("2026-09-02T00:00:00.000000Z");
    expect(evaluationFilterSchema.safeParse({ from: "yesterday" }).success).toBe(false);
  });
});
