import { describe, expect, it } from "vitest";

import type { GenerationRecord } from "@contracts/record";

import { evaluation, forestDetail, forestRecord, tableEntry } from "../test/fixtures";
import { buildGraph } from "./graph";
import { batchLabel, batchOfRunId, tableFacts, type TableFacts } from "./tables";

const byName = (facts: TableFacts[]) => new Map(facts.map((f) => [f.name, f]));

function forest(record: GenerationRecord | null = forestRecord()) {
  const detail = forestDetail();
  const graph = buildGraph(detail, null, [], record?.declared_tables);
  return byName(tableFacts({ detail, graph, record }));
}

describe("tableFacts", () => {
  it("reads the key, the source override and the batch job from the record", () => {
    const facts = forest();
    expect(facts.size).toBe(120);
    expect(facts.get("T_001")).toMatchObject({
      pk: "declared",
      pkColumns: ["COL_001"],
      degree: 0,
      batch: 0,
      sourceOverride: null,
      enabled: true,
      from: "record",
    });
    expect(facts.get("T_003")).toMatchObject({ pk: "none", pkColumns: [] });
    expect(facts.get("T_007")!.sourceOverride).toBe("example_archive.T_007_V2");
    expect(facts.get("T_033")!.batch).toBe(3);
  });

  it("a table whose key check failed is a FAIL whatever its overall score", () => {
    const t = forest().get("T_004")!;
    expect([t.status, t.keyFailures, t.score]).toEqual(["fail", 1, 0.88]);
  });

  it("otherwise takes the status of its overall score", () => {
    const facts = forest();
    expect(["T_001", "T_005", "T_010"].map((n) => facts.get(n)!.status)).toEqual(["pass", "warn", "fail"]);
  });

  it("falls back to the metric rows and the run ids for a run without a record", () => {
    const facts = forest(null);
    expect(facts.get("T_003")).toMatchObject({ pk: "none", from: "registry", sourceOverride: null, enabled: null });
    expect(facts.get("T_001")).toMatchObject({ pk: "declared", batch: 0 });
    expect(facts.get("T_033")!.batch).toBe(3);
  });

  it("says unknown when nothing tells", () => {
    const detail = { evaluation: evaluation({ base_run_id: null, tables: [tableEntry("A_TABLE")] }), metrics: [] };
    const [a] = tableFacts({ detail, graph: buildGraph(detail), record: null });
    expect(a).toMatchObject({ name: "A_TABLE", pk: "unknown", status: "not_evaluated", batch: null, score: null });
  });

  it("leaves external parents out", () => {
    const detail = {
      evaluation: evaluation({ tables: [tableEntry("orders"), tableEntry("products", { role: "external" })] }),
      metrics: [],
    };
    expect(tableFacts({ detail, graph: buildGraph(detail), record: null }).map((f) => f.name)).toEqual(["orders"]);
  });
});

describe("batch jobs", () => {
  it.each([
    ["F-b03-07-T_038", "F", 3],
    ["F-b11-01-T_119", "F", 11],
    ["F-00-users", "F", null],
    // The batch job's own run id `<base>-bNN` carries no table: no validation_runs row has it.
    ["F-b03", "F", null],
    ["F-b3-07-T_038", "F", null],
    ["X-b03-00-T_001", "F", null],
    [null, "F", null],
    ["F-b03-07-T_038", null, null],
  ])("%s under base %s is batch %s", (runId, base, batch) => {
    expect(batchOfRunId(runId, base)).toBe(batch);
  });
  it("labels them b00 …", () => {
    expect([batchLabel(0), batchLabel(11), batchLabel(123)]).toEqual(["b00", "b11", "b123"]);
  });
});
