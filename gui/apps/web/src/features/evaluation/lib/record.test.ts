import { describe, expect, it } from "vitest";

import { dataflowJobUrl, projectOf, shortVersion } from "./record";

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
