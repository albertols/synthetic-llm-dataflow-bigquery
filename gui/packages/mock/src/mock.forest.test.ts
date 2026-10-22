/**
 * The isolated-forest launch (ADR 0045): one whole-model launch of 120 unrelated tables, 112 of
 * them generated in 12 batch jobs, and its evaluation — the mock's case for the run view's
 * isolated-tables views. Every fact of the model is a rule on the table's number (forest.ts).
 */
import {
  buildGenerationRecord,
  inapplicableKey,
  NO_PK_REASON,
  parseSnapshot,
  type EvaluationDataHistoryRow,
} from "@synthetic-platform/contracts";
import { beforeAll, describe, expect, it } from "vitest";

import {
  createMockDataset,
  FOREST_BATCHES,
  FOREST_EVALUATION,
  FOREST_RELATIONSHIP_MODEL,
  type MockDataset,
} from "./index";

let data: MockDataset;
let final: EvaluationDataHistoryRow;
beforeAll(() => {
  data = createMockDataset();
  final = data.registry.filter((r) => r.evaluation_id === FOREST_EVALUATION).at(-1)!;
}, 120_000);

const runsOf = () => data.validationRuns.filter((r) => r.launch_run_id === final.base_run_id);
const metricsOf = () => data.metrics.filter((m) => m.evaluation_id === FOREST_EVALUATION);
const range = (from: number, to: number) =>
  Array.from({ length: to - from + 1 }, (_, k) => `T_${String(from + k).padStart(3, "0")}`);

describe("the isolated-forest launch (ADR 0045)", { timeout: 60_000 }, () => {
  it("declares 120 unrelated tables and generates the 112 enabled ones in 12 batches of at most 10", () => {
    expect(FOREST_RELATIONSHIP_MODEL.tables).toHaveLength(120);
    expect(FOREST_RELATIONSHIP_MODEL.tables.every((t) => t.fk.length === 0)).toBe(true);
    expect(FOREST_RELATIONSHIP_MODEL.tables.filter((t) => !t.enabled).map((t) => t.name)).toEqual([
      "T_015",
      "T_030",
      "T_045",
      "T_060",
      "T_075",
      "T_090",
      "T_105",
      "T_120",
    ]);
    // The optional RelationTable keys, present only where the model sets them (ADR 0045 D2, Ruling 9).
    expect(FOREST_RELATIONSHIP_MODEL.tables.filter((t) => t.source_table).map((t) => t.name)).toEqual([
      "T_007",
      "T_027",
      "T_047",
      "T_067",
      "T_087",
      "T_107",
    ]);
    expect(FOREST_RELATIONSHIP_MODEL.tables.filter((t) => t.num_rows === 5000)).toHaveLength(5);
    expect(FOREST_RELATIONSHIP_MODEL.tables.filter((t) => "num_rows" in t)).toHaveLength(5);
    expect(FOREST_BATCHES.map((b) => b.length)).toEqual([10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 2]);
    expect(FOREST_BATCHES[3]).toEqual(range(33, 42));
    expect(FOREST_BATCHES[11]).toEqual(["T_118", "T_119"]);
    expect(data.registry.filter((r) => r.evaluation_id === FOREST_EVALUATION).map((r) => r.event)).toEqual([
      "RUNNING",
      "FINAL",
    ]);
    expect(final.tables).toHaveLength(112);
    expect(final.tables.every((t) => t.role === "isolated")).toBe(true);
    expect(final.relationship_model).toBe("isolated_forest_demo");
    expect(final.relationship_model_sha).toBe(FOREST_RELATIONSHIP_MODEL.sha12);
  });

  it("names its runs <base>-bNN-MM-<table>; each batch is one Dataflow job carrying one snapshot", () => {
    const base = final.base_run_id!;
    expect(final.run_ids[0]).toBe(`${base}-b00-00-T_001`);
    expect(final.run_ids.at(-1)).toBe(`${base}-b11-01-T_119`);
    const runs = runsOf();
    expect(runs).toHaveLength(112);
    expect(new Set(runs.map((r) => r.job_id)).size).toBe(12);
    expect(new Set(runs.map((r) => r.config_digest)).size).toBe(1);
    expect(final.generator_config_digest).toBe(runs[0]!.config_digest);
    expect(final.generation_job_id).toBe(runs.find((r) => r.run_id === `${base}-b00-00-T_001`)!.job_id);
    // The model DAG's one chained evaluation records every batch job that landed, in batch order,
    // the first as generation_job_id (ADR 0045 Rulings 2, 5 and 18).
    const jobOf = (b: number) =>
      runs.find((r) => r.run_id.startsWith(`${base}-b${String(b).padStart(2, "0")}-`))!.job_id;
    expect(final.generation_job_ids).toEqual(FOREST_BATCHES.map((_, b) => jobOf(b)));
    expect(final.generation_job_ids[0]).toBe(final.generation_job_id);
    expect(final.trigger).toBe("chained");
    expect(final.params_source).toBe("validation_runs");
    expect(final.generation_params).toMatchObject({ tables: "all", max_tables_per_job: 10, prep_workers: 8 });
    expect(final.generation_params).not.toHaveProperty("landing_table");
    for (const run of runs) {
      const snapshot = parseSnapshot(run.config)!;
      const batch = snapshot.batch as { index: number; count: number; tables: string[] };
      expect(run.run_id.startsWith(`${base}-b${String(batch.index).padStart(2, "0")}-`), run.run_id).toBe(true);
      expect(batch.count).toBe(12);
      // The batch's Dataflow job name is the model DAG's job name plus -bNN, never its run id.
      expect(run.job_name).toBe(`synthetic-sdfb-model-v0-5-3-gemma4-e4b-b${String(batch.index).padStart(2, "0")}`);
      // This job's tables only, as declared: disabled tables never appear; ADR 0045's two keys on every one.
      expect(Object.keys(snapshot.relationships!.tables!)).toEqual(batch.tables);
      for (const declared of Object.values(snapshot.relationships!.tables!))
        expect(Object.keys(declared as object)).toEqual(expect.arrayContaining(["source_table", "num_rows"]));
      // Exactly the four settable launch keys; the batch identity is the batch block only.
      expect(snapshot.values!.launch).toEqual({
        tables: "all",
        max_tables_per_job: 10,
        max_parallel_jobs: 1,
        prep_workers: 8,
      });
      expect(snapshot.values!.generation!.engine).toBe(final.engine);
      expect(snapshot.values!.serving!.model_uri).toBe(final.llm_model_uri);
    }
  });

  it("reads back as one record of 112 declared tables and 12 batch jobs", () => {
    const record = buildGenerationRecord(final, [...runsOf()].reverse());
    expect(record.declared_tables).toHaveLength(112);
    expect(record.declared_tables.filter((t) => t.pk.length === 0)).toHaveLength(32);
    expect(record.declared_tables.filter((t) => t.source_table).map((t) => t.name)).toEqual([
      "T_007",
      "T_027",
      "T_047",
      "T_067",
      "T_087",
      "T_107",
    ]);
    expect(record.declared_tables.filter((t) => t.num_rows === 5000).map((t) => t.name)).toEqual([
      "T_011",
      "T_036",
      "T_061",
      "T_086",
      "T_111",
    ]);
    expect(record.batches.map((b) => b.index)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    expect(record.batches.every((b) => b.job_ids.length === 1)).toBe(true);
    expect(record.config_digests).toHaveLength(1);
  });

  it("source overrides land in validation_runs.reference_table and the registry's source_table", () => {
    const run = runsOf().find((r) => r.landing_table?.endsWith(".T_007"))!;
    expect(run.reference_table).toBe("demo-project.example_archive.T_007_V2");
    expect(final.tables.find((t) => t.name === "T_007")!.source_table).toBe("demo-project.example_archive.T_007_V2");
    expect(runsOf().find((r) => r.landing_table?.endsWith(".T_011"))!.num_rows_requested).toBe(5000);
  });

  it("writes the evaluator's rows: N/A key checks with its reasons, two duplicate-key FAILs, no relationship rows", () => {
    const metrics = metricsOf();
    const pk = metrics.filter((m) => m.metric_id === "table.pk_duplicate_rate");
    expect(pk).toHaveLength(112);
    expect(pk.filter((m) => inapplicableKey(m) === "pk")).toHaveLength(32);
    expect((pk.find((m) => m.table_name === "T_003")!.detail as { reason?: unknown }).reason).toBe(NO_PK_REASON);
    expect(
      pk
        .filter((m) => m.status === "fail")
        .map((m) => m.table_name)
        .sort(),
    ).toEqual(["T_004", "T_008"]);
    const identity = metrics.filter((m) => m.metric_id === "table.identity_duplicate_rate");
    expect(identity).toHaveLength(112);
    expect(identity.every((m) => inapplicableKey(m) === "identity")).toBe(true);
    expect(metrics.some((m) => m.level === "relationship")).toBe(false);
  });
});
