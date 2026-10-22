import { describe, expect, it } from "vitest";

import type { GenerationRecord } from "@contracts/record";
import type { RelationshipModel } from "@contracts/relational";

import { evaluation, forestDetail, forestRecord, richDetail, tableEntry } from "../test/fixtures";
import { buildGraph } from "./graph";
import { batchLabel, batchOfRunId, declaresNoEdges, modelSummary, tableFacts, type TableFacts } from "./tables";

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

describe("modelSummary", () => {
  const detail = forestDetail();
  const record = forestRecord();
  const graph = buildGraph(detail, null, [], record.declared_tables);
  const facts = tableFacts({ detail, graph, record });
  const served = (sha12: string): RelationshipModel => ({
    model: "isolated_forest_demo",
    description: "",
    source: "",
    sha12,
    generation_order: [],
    tables: Array.from({ length: 125 }, (_, k) => ({
      name: `T_${String(k + 1).padStart(3, "0")}`,
      pk: [],
      identity: [],
      enabled: k < 120,
      note: "",
      fk: [],
    })),
  });

  it("counts what the launch declared, from the record", () => {
    expect(modelSummary({ evaluation: detail.evaluation, record, facts, graph })).toMatchObject({
      model: "isolated_forest_demo",
      sha12: "f0f0f0f0f0f0",
      source: "record",
      tables: 120,
      declaredTotal: null,
      disabled: null,
      withPk: 80,
      withoutPk: 40,
      pkUnknown: 0,
      edges: 0,
      isolated: 120,
      sourceOverrides: 6,
      batchCount: 12,
    });
  });

  it("counts disabled tables only from a served model with the record's fingerprint", () => {
    expect(
      modelSummary({ evaluation: detail.evaluation, record, facts, graph, model: served("f0f0f0f0f0f0") }),
    ).toMatchObject({ declaredTotal: 125, disabled: 5 });
    expect(
      modelSummary({ evaluation: detail.evaluation, record, facts, graph, model: served("0123456789ab") }),
    ).toMatchObject({ declaredTotal: null, disabled: null });
  });

  it("counts the batch jobs planned and the ones recorded", () => {
    const partial = { ...record, batches: record.batches.filter((b) => b.index !== 5) };
    const summary = modelSummary({ evaluation: detail.evaluation, record: partial, facts, graph });
    expect([summary.batchCount, summary.batches.length]).toEqual([12, 11]);
  });

  it("says it reads the registry when there is no record", () => {
    const bare = tableFacts({ detail, graph: buildGraph(detail), record: null });
    expect(
      modelSummary({ evaluation: detail.evaluation, record: null, facts: bare, graph: buildGraph(detail) }),
    ).toMatchObject({
      source: "registry",
      sha12: "f0f0f0f0f0f0",
      sourceOverrides: null,
      batchCount: null,
      withoutPk: 40,
    });
  });
});

describe("declaresNoEdges", () => {
  it("is true when a record, a model or every role says the launch declared no foreign key", () => {
    const detail = forestDetail(12);
    const record = forestRecord(12);
    const fromRecord = buildGraph(detail, null, [], record.declared_tables);
    expect(
      declaresNoEdges({
        facts: tableFacts({ detail, graph: fromRecord, record }),
        graph: fromRecord,
        evaluation: detail.evaluation,
      }),
    ).toBe(true);
    // No record: every registry role is `isolated`.
    const bare = buildGraph(detail);
    expect(
      declaresNoEdges({ facts: tableFacts({ detail, graph: bare }), graph: bare, evaluation: detail.evaluation }),
    ).toBe(true);
  });

  it("is false for one table, for a drawn edge, and when nothing says so", () => {
    const one = forestDetail(1);
    const g1 = buildGraph(one);
    expect(
      declaresNoEdges({ facts: tableFacts({ detail: one, graph: g1 }), graph: g1, evaluation: one.evaluation }),
    ).toBe(false);
    const rich = richDetail();
    const g2 = buildGraph(rich);
    expect(
      declaresNoEdges({ facts: tableFacts({ detail: rich, graph: g2 }), graph: g2, evaluation: rich.evaluation }),
    ).toBe(false);
    const unknown = { evaluation: evaluation({ tables: [tableEntry("A_TABLE"), tableEntry("B_TABLE")] }), metrics: [] };
    const g3 = buildGraph(unknown);
    expect(
      declaresNoEdges({ facts: tableFacts({ detail: unknown, graph: g3 }), graph: g3, evaluation: unknown.evaluation }),
    ).toBe(false);
  });
});
