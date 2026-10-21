import { describe, expect, it } from "vitest";
import { bqTables } from "../generated/schemas";
import { EMPTY_RECORD_COLUMNS, RECORD_COLUMNS } from "./record";

describe("record columns", () => {
  it("are the last fourteen columns of validation_runs, in order", () => {
    const names = bqTables.validation_runs.fields.map((f) => f.name);
    expect(names.slice(-RECORD_COLUMNS.length)).toEqual([...RECORD_COLUMNS]);
  });
  it("empty means every one null", () => {
    expect(Object.values(EMPTY_RECORD_COLUMNS).every((v) => v === null)).toBe(true);
  });
});
