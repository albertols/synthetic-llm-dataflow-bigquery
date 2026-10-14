import { describe, expect, it } from "vitest";

import { parseSource, repoBlobUrl } from "./links";

describe("links", () => {
  it("builds GitHub blob URLs with an optional line anchor", () => {
    expect(repoBlobUrl("packages/a.py", 12)).toBe(
      "https://github.com/albertols/synthetic-llm-dataflow-bigquery/blob/master/packages/a.py#L12",
    );
    expect(repoBlobUrl("./docs/DESIGN.md", undefined, "v0.5.3")).toBe(
      "https://github.com/albertols/synthetic-llm-dataflow-bigquery/blob/v0.5.3/docs/DESIGN.md",
    );
  });

  it("parses knob sources", () => {
    expect(parseSource("packages/sdfb-core/src/x.py:42")).toEqual({ path: "packages/sdfb-core/src/x.py", line: 42 });
    expect(parseSource("config/thresholds.yml")).toEqual({ path: "config/thresholds.yml" });
  });
});
