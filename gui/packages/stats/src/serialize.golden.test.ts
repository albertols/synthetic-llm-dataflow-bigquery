/**
 * GReaT serialization must render exactly what `sdfb_core.rag.serialize.serialize_row`
 * renders: Python's str() of ints, floats (repr: 1.0, 1e-05, 1e+16, -0.0),
 * booleans (true/false), None (null), datetimes (space separator), dates and Decimals.
 */
import { describe, expect, it } from "vitest";

import golden from "@contracts/generated/golden/great_serialize.json";

import { type GreatValue, pyFloatRepr, serializeGreat } from "./great";

type Cell = { t: string; v?: unknown };

function decode(row: Record<string, Cell>): Record<string, GreatValue> {
  const out: Record<string, GreatValue> = {};
  for (const [column, cell] of Object.entries(row)) {
    switch (cell.t) {
      case "null":
        out[column] = null;
        break;
      case "int":
        out[column] = typeof cell.v === "string" ? BigInt(cell.v) : (cell.v as number);
        break;
      case "float":
        out[column] = { py: "float", value: cell.v as number };
        break;
      case "bool":
        out[column] = cell.v as boolean;
        break;
      case "datetime":
        out[column] = { py: "datetime", value: cell.v as string };
        break;
      case "date":
        out[column] = { py: "date", value: cell.v as string };
        break;
      case "decimal":
        out[column] = { py: "decimal", value: cell.v as string };
        break;
      default:
        out[column] = cell.v as string;
    }
  }
  return out;
}

describe("GReaT serialization port", () => {
  it("renders every golden row exactly", () => {
    expect(golden.rows).toHaveLength(10);
    for (const testCase of golden.rows) {
      expect(serializeGreat(decode(testCase.row as Record<string, Cell>), golden.column_order)).toBe(testCase.text);
    }
  });

  it("formats floats like Python's repr", () => {
    const cases: [number, string][] = [
      [1, "1.0"],
      [0.1, "0.1"],
      [1e16, "1e+16"],
      [1e15, "1000000000000000.0"],
      [1e-5, "1e-05"],
      [1e-4, "0.0001"],
      [-0, "-0.0"],
      [0, "0.0"],
      [2.5e-7, "2.5e-07"],
      [123, "123.0"],
      [1e22, "1e+22"],
      [1.5e300, "1.5e+300"],
      [-42.125, "-42.125"],
      [0.30000000000000004, "0.30000000000000004"],
      [Number.NaN, "nan"],
      [Number.POSITIVE_INFINITY, "inf"],
      [Number.NEGATIVE_INFINITY, "-inf"],
    ];
    for (const [value, expected] of cases) expect(pyFloatRepr(value)).toBe(expected);
  });

  it("renders integer-valued numbers in float columns with .0", () => {
    expect(serializeGreat({ a: 42, b: 42 }, ["a", "b"], { floatColumns: ["b"] })).toBe("a is 42, b is 42.0");
    expect(serializeGreat({}, ["missing"])).toBe("missing is null");
  });
});
