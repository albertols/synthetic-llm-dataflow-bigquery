import { describe, expect, it } from "vitest";
import { bqTables, type ValidationRunsRow } from "../generated/schemas";
import {
  EMPTY_RECORD_COLUMNS,
  RECORD_COLUMNS,
  buildGenerationRecord,
  configDiff,
  flattenValues,
  parseBatch,
  parseDeclaredTables,
  parseGeneratorVersion,
  parseSnapshot,
} from "./record";

describe("record columns", () => {
  it("are the last fourteen columns of validation_runs, in order", () => {
    const names = bqTables.validation_runs.fields.map((f) => f.name);
    expect(names.slice(-RECORD_COLUMNS.length)).toEqual([...RECORD_COLUMNS]);
  });
  it("empty means every one null", () => {
    expect(Object.values(EMPTY_RECORD_COLUMNS).every((v) => v === null)).toBe(true);
  });
});

const SNAPSHOT = {
  config_version: 1,
  values: { generation: { similarity: 0.7, seed: "" }, guardrails: { repeat_share_tolerance: 0.08 } },
  provenance: { "generation.similarity": "file", "generation.seed": "default" },
  overridden: [{ key: "generation.similarity", typed: 0.3, file: 0.7 }],
  launcher: { version: "0.6.1-master-edcfdcd" },
  future_block: { anything: true },
};

function row(over: Partial<ValidationRunsRow>): ValidationRunsRow {
  return {
    run_id: "B-00-users",
    reference_digest: null,
    reference_table: null,
    landing_table: "p.d.users",
    engine: null,
    model_uri: null,
    env: null,
    num_rows_requested: null,
    valid_count: null,
    dlq_count: null,
    blocker_count: null,
    dlq_by_rule: null,
    blocker_failure_ratio: null,
    observed_blocker_ratio: null,
    status: null,
    excluded_blocker_rules: null,
    source_repeat_share: null,
    landing_repeat_share: null,
    repeat_share_delta: null,
    repeat_share_within_tolerance: null,
    created_at: "2026-10-21T10:00:00.000000Z",
    ...EMPTY_RECORD_COLUMNS,
    ...over,
  };
}

const EVALUATION = {
  evaluation_id: "e1",
  run_ids: ["B-00-users", "B-01-orders"],
  base_run_id: "B",
  generation_job_id: "J",
};

describe("parseSnapshot", () => {
  it.each(["", "{not json", "[1,2]", "42", "null"])("is null for %j", (text) => {
    expect(parseSnapshot(text)).toBeNull();
  });
  it("keeps unknown blocks", () => {
    expect(parseSnapshot(JSON.stringify(SNAPSHOT))?.future_block).toEqual({ anything: true });
  });
  // Final review M3: a `values` that is not an object is no snapshot; an `overridden` entry that is not one is skipped.
  it.each(['{"values": 42}', '{"values": "text"}', '{"values": [1]}', '{"values": null}'])("is null for %s", (text) => {
    expect(parseSnapshot(text)).toBeNull();
  });
  it("keeps only the overridden entries that are objects with a key", () => {
    const text = JSON.stringify({
      values: {},
      overridden: [null, 5, "x", { key: 7 }, { key: "a.b", typed: 1, file: 2 }],
    });
    expect(parseSnapshot(text)?.overridden).toEqual([{ key: "a.b", typed: 1, file: 2 }]);
  });
  it("drops an overridden that is not a list", () => {
    expect(parseSnapshot(JSON.stringify({ values: {}, overridden: { key: "a.b" } }))?.overridden).toBeUndefined();
  });
});

describe("buildGenerationRecord", () => {
  it("reads the snapshot, the versions and the digests of this launch's rows", () => {
    const record = buildGenerationRecord(EVALUATION, [
      row({
        run_id: "B-01-orders",
        generator_version: "0.6.1-master-edcfdcd",
        config_digest: "d1",
        config: JSON.stringify(SNAPSHOT),
      }),
      row({
        run_id: "B-00-users",
        generator_version: "0.6.1-master-edcfdcd",
        config_digest: "d1",
        config: JSON.stringify(SNAPSHOT),
      }),
    ]);
    expect(record.runs.map((r) => r.run_id)).toEqual(["B-00-users", "B-01-orders"]);
    expect(record.snapshot_run_id).toBe("B-00-users");
    expect(record.launcher_version).toBe("0.6.1-master-edcfdcd");
    expect(record.version_mismatch).toBe(false);
    expect(record.config_digests).toEqual(["d1"]);
  });
  it("flags a worker that ran other code than the launcher", () => {
    const record = buildGenerationRecord(EVALUATION, [
      row({ generator_version: "0.6.1-master-1111111", config: JSON.stringify(SNAPSHOT) }),
    ]);
    expect(record.version_mismatch).toBe(true);
    expect(record.worker_versions).toEqual(["0.6.1-master-1111111"]);
  });
  it("drops another launch's rows", () => {
    const record = buildGenerationRecord(EVALUATION, [
      row({ run_id: "OTHER-00-users", job_id: "K", config: JSON.stringify(SNAPSHOT) }),
    ]);
    expect(record.runs).toEqual([]);
    expect(record.snapshot).toBeNull();
  });
  it("drops another job's row under a reused run id, keeping the job's own (final review M1)", () => {
    const foreign = { ...SNAPSHOT, values: { generation: { similarity: 0.3, seed: "" } }, launcher: { version: "x" } };
    // Newest first: the foreign job relaunched under the same --run_id after this evaluation's job.
    const record = buildGenerationRecord(EVALUATION, [
      row({ run_id: "B-00-users", job_id: "K", config_digest: "dK", config: JSON.stringify(foreign) }),
      row({ run_id: "B-01-orders", job_id: "K", config_digest: "dK", config: JSON.stringify(foreign) }),
      row({ run_id: "B-00-users", job_id: "J", config_digest: "dJ", config: JSON.stringify(SNAPSHOT) }),
    ]);
    expect(record.runs.map((r) => [r.run_id, r.job_id])).toEqual([["B-00-users", "J"]]);
    expect(record.snapshot?.values).toEqual(SNAPSHOT.values);
    expect(record.config_digests).toEqual(["dJ"]);
  });
  it("keeps a listed run without a job id beside the job's own rows", () => {
    const record = buildGenerationRecord(EVALUATION, [
      row({ run_id: "B-01-orders", job_id: null }),
      row({ run_id: "B-00-users", job_id: "J", config: JSON.stringify(SNAPSHOT) }),
    ]);
    expect(record.runs.map((r) => r.run_id)).toEqual(["B-00-users", "B-01-orders"]);
  });
  it("keeps a row by its job id alone", () => {
    const record = buildGenerationRecord(EVALUATION, [row({ run_id: "X-00-users", job_id: "J" })]);
    expect(record.runs.map((r) => r.run_id)).toEqual(["X-00-users"]);
  });
  it("keeps the newest row of a run id (rows arrive newest first)", () => {
    const record = buildGenerationRecord(EVALUATION, [row({ config_digest: "new" }), row({ config_digest: "old" })]);
    expect(record.config_digests).toEqual(["new"]);
  });
  it("is empty, not an error, for rows written before the record", () => {
    const record = buildGenerationRecord(EVALUATION, [row({})]);
    expect(record.snapshot).toBeNull();
    expect(record.launcher_version).toBeNull();
    expect(record.version_mismatch).toBe(false);
  });
});

describe("declared tables and batch jobs (whole-model launches, ADR 0045 D7)", () => {
  const LAUNCH = {
    evaluation_id: "e2",
    run_ids: [],
    base_run_id: "L",
    generation_job_id: "J0",
    generation_job_ids: ["J0", "J1"],
  };
  const B00 = { index: 0, count: 2, tables: ["T_A", "T_B"] };
  // As B writes them (`TableRelations.model_dump(mode="json")`): both keys always present, null when unset.
  const TABLES_B00 = {
    T_A: {
      pk: ["COL_001"],
      identity: [],
      fk: [],
      enabled: true,
      note: "",
      source_table: "example_archive.T_A_V2",
      num_rows: null,
    },
    T_B: { pk: [], identity: [], fk: [], enabled: true, note: "", source_table: null, num_rows: null },
  };
  const snap = (batch: unknown, tables: Record<string, unknown>) =>
    JSON.stringify({ ...SNAPSHOT, relationships: { model: "m", sha12: "s", tables }, batch });
  const one = (name: string) => ({
    [name]: { pk: [], identity: [], fk: [], enabled: true, note: "", source_table: null, num_rows: null },
  });

  it("unions every row's declared tables and groups the rows by batch job", () => {
    const record = buildGenerationRecord(LAUNCH, [
      row({
        run_id: "L-b01-00-T_C",
        job_id: "J1",
        // An A2-era table entry (no source_table / num_rows keys) reads as null.
        config: snap({ index: 1, count: 2, tables: ["T_C"] }, { T_C: { pk: [], identity: [], fk: [], enabled: true } }),
      }),
      row({ run_id: "L-b00-01-T_B", job_id: "J0", config: snap(B00, TABLES_B00) }),
      row({ run_id: "L-b00-00-T_A", job_id: "J0", config: snap(B00, TABLES_B00) }),
    ]);
    expect(record.declared_tables.map((t) => [t.name, t.pk, t.source_table])).toEqual([
      ["T_A", ["COL_001"], "example_archive.T_A_V2"],
      ["T_B", [], null],
      ["T_C", [], null],
    ]);
    expect(record.batches).toEqual([
      { index: 0, count: 2, tables: ["T_A", "T_B"], job_ids: ["J0"], run_ids: ["L-b00-00-T_A", "L-b00-01-T_B"] },
      { index: 1, count: 2, tables: ["T_C"], job_ids: ["J1"], run_ids: ["L-b01-00-T_C"] },
    ]);
  });

  it("a batch whose rows are missing is absent; the count still says how many were planned", () => {
    const record = buildGenerationRecord(LAUNCH, [
      row({ run_id: "L-b00-00-T_A", job_id: "J0", config: snap(B00, TABLES_B00) }),
    ]);
    expect(record.batches.map((b) => [b.index, b.count])).toEqual([[0, 2]]);
  });

  it("leaves out the rows of a batch job the evaluation did not cover; a row with no job id stays", () => {
    const record = buildGenerationRecord({ ...LAUNCH, generation_job_ids: ["J0"] }, [
      row({ run_id: "L-b00-00-T_A", job_id: "J0", config: snap({ index: 0, count: 3, tables: ["T_A"] }, one("T_A")) }),
      // b01's job FAILED after writing its row (a BLOCKER): B's tables=all launch did not list it.
      row({ run_id: "L-b01-00-T_C", job_id: "J1", config: snap({ index: 1, count: 3, tables: ["T_C"] }, one("T_C")) }),
      row({ run_id: "L-b02-00-T_D", job_id: null, config: snap({ index: 2, count: 3, tables: ["T_D"] }, one("T_D")) }),
    ]);
    expect(record.batches.map((b) => b.index)).toEqual([0, 2]);
    expect(record.declared_tables.map((t) => t.name)).toEqual(["T_A", "T_D"]);
    // The failed job's row leaves A2's fields too: the record is the rows of the jobs the evaluation lists.
    expect(record.runs.map((r) => r.run_id)).toEqual(["L-b00-00-T_A", "L-b02-00-T_D"]);
    expect(record.runs).toHaveLength(2);
  });

  it("an unlisted job's row under a listed run id is dropped", () => {
    const listed = { ...LAUNCH, run_ids: ["L-b00-00-T_A", "L-b01-00-T_C"] };
    // Newest first: job K relaunched under the same --run_id after this launch's batch job J1.
    const record = buildGenerationRecord(listed, [
      row({
        run_id: "L-b01-00-T_C",
        job_id: "K",
        config_digest: "dK",
        config: snap({ index: 0, count: 1, tables: ["T_X"] }, one("T_X")),
      }),
      row({
        run_id: "L-b01-00-T_C",
        job_id: "J1",
        config_digest: "d",
        config: snap({ index: 1, count: 2, tables: ["T_C"] }, one("T_C")),
      }),
      row({
        run_id: "L-b00-00-T_A",
        job_id: "J0",
        config_digest: "d",
        config: snap({ index: 0, count: 2, tables: ["T_A"] }, one("T_A")),
      }),
    ]);
    expect(record.runs.map((r) => [r.run_id, r.job_id])).toEqual([
      ["L-b00-00-T_A", "J0"],
      ["L-b01-00-T_C", "J1"],
    ]);
    expect(record.config_digests).toEqual(["d"]);
    expect(record.declared_tables.map((t) => t.name)).toEqual(["T_A", "T_C"]);
    expect(record.batches.map((b) => [b.index, b.job_ids])).toEqual([
      [0, ["J0"]],
      [1, ["J1"]],
    ]);
  });

  it("keeps a row by any listed job id alone", () => {
    const record = buildGenerationRecord(LAUNCH, [row({ run_id: "X-b01-00-T_C", job_id: "J1" })]);
    expect(record.runs.map((r) => r.run_id)).toEqual(["X-b01-00-T_C"]);
  });

  it("a pre-migration row (`generation_job_ids: []`) narrows to `generation_job_id`", () => {
    const record = buildGenerationRecord({ ...LAUNCH, generation_job_ids: [] }, [
      row({ run_id: "L-00-T_A", job_id: "K", config_digest: "dK" }),
      row({ run_id: "L-00-T_A", job_id: "J0", config_digest: "dJ" }),
      row({ run_id: "L-01-T_B", job_id: "J1", config_digest: "dJ1" }),
    ]);
    expect(record.runs.map((r) => [r.run_id, r.job_id])).toEqual([["L-00-T_A", "J0"]]);
    expect(record.config_digests).toEqual(["dJ"]);
  });

  it("reads B's optional keys only when they are well formed", () => {
    const parsed = parseDeclaredTables({
      relationships: {
        tables: {
          T_A: {
            pk: ["COL_001", 7],
            fk: [{ cols: ["COL_002"], ref: "T_B", ref_cols: ["COL_001"], enforced: false }, { cols: [] }],
            num_rows: 0,
            source_table: "",
          },
          T_B: { enabled: "yes", num_rows: 5000 },
          T_C: "not an object",
        },
      },
    });
    expect(parsed).toEqual([
      {
        name: "T_A",
        pk: ["COL_001"],
        identity: [],
        fk: [{ cols: ["COL_002"], ref: "T_B", ref_cols: ["COL_001"], enforced: false }],
        enabled: null,
        source_table: null,
        num_rows: null,
      },
      { name: "T_B", pk: [], identity: [], fk: [], enabled: null, source_table: null, num_rows: 5000 },
    ]);
  });

  it("is empty for a snapshot without relationships or batch, and for a malformed batch", () => {
    expect(parseDeclaredTables(null)).toEqual([]);
    expect(parseBatch({ batch: null })).toBeNull();
    expect(parseBatch({ batch: { index: 2, count: 2, tables: [] } })).toBeNull();
    expect(parseBatch({ batch: { index: "0", count: 1 } })).toBeNull();
    const record = buildGenerationRecord(LAUNCH, [row({ run_id: "L-00-T_A", config: JSON.stringify(SNAPSHOT) })]);
    expect([record.declared_tables, record.batches]).toEqual([[], []]);
  });
});

describe("config diff", () => {
  it("flattens values to dotted keys", () => {
    expect(flattenValues(SNAPSHOT)).toEqual({
      "generation.similarity": 0.7,
      "generation.seed": "",
      "guardrails.repeat_share_tolerance": 0.08,
    });
  });
  it("lists only the keys that differ, with provenance", () => {
    const other = { ...SNAPSHOT, values: { ...SNAPSHOT.values, generation: { similarity: 0.5, seed: "" } } };
    expect(configDiff([SNAPSHOT, other])).toEqual([
      { key: "generation.similarity", values: [0.7, 0.5], provenance: ["file", "file"] },
    ]);
  });
  it("needs two snapshots", () => {
    expect(configDiff([SNAPSHOT, null])).toEqual([]);
  });
});

describe("parseGeneratorVersion", () => {
  it.each([
    ["0.6.1-master-edcfdcd", { base: "0.6.1", branch: "master", sha: "edcfdcd", dirty: false }],
    [
      "0.6.1-feat-config-record-model-1a2b3c4-dirty",
      { base: "0.6.1", branch: "feat-config-record-model", sha: "1a2b3c4", dirty: true },
    ],
    ["0.6.1-unknown-unknown", { base: "0.6.1", branch: "unknown", sha: "unknown", dirty: false }],
  ])("%s", (text, parsed) => {
    expect(parseGeneratorVersion(text)).toEqual(parsed);
  });
  it("is null for anything else", () => {
    expect(parseGeneratorVersion("0.1.0")).toBeNull();
    expect(parseGeneratorVersion(null)).toBeNull();
  });
});
