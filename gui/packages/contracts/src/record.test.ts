import { describe, expect, it } from "vitest";
import { bqTables, type ValidationRunsRow } from "../generated/schemas";
import {
  EMPTY_RECORD_COLUMNS,
  RECORD_COLUMNS,
  buildGenerationRecord,
  configDiff,
  flattenValues,
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
