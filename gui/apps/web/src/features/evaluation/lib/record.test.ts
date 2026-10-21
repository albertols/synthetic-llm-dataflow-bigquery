import { describe, expect, it } from "vitest";

import { generationRecord } from "../test/fixtures";
import { dataflowJobUrl, projectOf, shortVersion, toleranceOf } from "./record";

describe("record links", () => {
  it("links a Dataflow job in its region and project", () => {
    expect(dataflowJobUrl("2026-10-21_01_02_03-123", "europe-west3", "p")).toBe(
      "https://console.cloud.google.com/dataflow/jobs/europe-west3/2026-10-21_01_02_03-123?project=p",
    );
  });
  it("reads the project of a table", () => {
    expect(projectOf("p.d.t")).toBe("p");
    expect(projectOf("p:d.t")).toBe("p");
    expect(projectOf("d.t")).toBeNull();
    expect(projectOf(null)).toBeNull();
  });
  it("shortens a version to base · sha", () => {
    expect(shortVersion("0.6.1-feat-config-record-model-1a2b3c4-dirty")).toBe("0.6.1 · 1a2b3c4*");
    expect(shortVersion(null)).toBe("—");
    expect(shortVersion("odd")).toBe("odd");
  });
});

describe("toleranceOf", () => {
  it("is the run's guardrails.repeat_share_tolerance", () => {
    expect(toleranceOf(generationRecord({ tolerance: 0.08 }))).toBe(0.08);
  });
  it("falls back to the default", () => {
    expect(toleranceOf(null)).toBe(0.05);
    expect(toleranceOf({ ...generationRecord(), snapshot: null })).toBe(0.05);
  });
});
