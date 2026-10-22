import { NO_IDENTITY_REASON, NO_PK_REASON } from "@contracts/applicability";
import { describe, expect, it } from "vitest";

import { metric } from "../test/fixtures";
import { integrityUnits, unitsMean } from "./integrity";

const T = "A_TABLE";

describe("integrityUnits (the evaluator's roll-up units, Ruling R11)", () => {
  const rows = [
    metric("field.type_validity", { table_name: T, column_name: "COL_001", score: 1 }),
    metric("field.type_validity", { table_name: T, column_name: "COL_002", score: 0.5 }),
    metric("column.source_stats_drift", { table_name: T, column_name: "COL_002", score: 0.9 }),
    metric("table.row_count_ratio", { table_name: T, score: 0.8 }),
    metric("table.pk_duplicate_rate", {
      table_name: T,
      value: null,
      score: null,
      status: "not_evaluated",
      detail: { reason: NO_PK_REASON },
    }),
    metric("table.identity_duplicate_rate", {
      table_name: T,
      value: null,
      score: null,
      status: "not_evaluated",
      detail: { reason: NO_IDENTITY_REASON },
    }),
    metric("relationship.orphan_rate", {
      table_name: T,
      edge: "A_TABLE(COL_003) -> B_TABLE(COL_001)",
      value: 0.01,
      score: null,
      status: "info",
      detail: { enforced: false, role: "documented" },
    }),
    // Not integrity units of A_TABLE: another family, another table, a roll-up.
    metric("column.ks", { table_name: T, column_name: "COL_002", score: 0.1 }),
    metric("field.type_validity", { table_name: "B_TABLE", column_name: "COL_001", score: 0 }),
    metric("table.integrity_score", { table_name: T, value: 0.8333, score: 0.8333 }),
  ];

  it("counts each column, each table-level check and each edge as child once; N/A and INFO units are left out", () => {
    const units = integrityUnits(rows, T);
    expect(units.map((u) => [u.label, u.state])).toEqual([
      ["COL_001", "counted"],
      ["COL_002", "counted"],
      ["Row count", "counted"],
      ["Identity duplicates", "not_applicable"],
      ["PK duplicates", "not_applicable"],
      ["A_TABLE(COL_003) -> B_TABLE(COL_001)", "not_scored"],
    ]);
    expect(units[1]!.score).toBeCloseTo(0.7, 12);
    expect(units[3]!.note).toBe("Not applicable — no identity columns declared");
    expect(units[5]!.note).toBe("informational: no score");
    expect(unitsMean(units)).toBeCloseTo((1 + 0.7 + 0.8) / 3, 12);
  });

  it("a key check not evaluated for another reason is not scored, not N/A", () => {
    const [unit] = integrityUnits(
      [
        metric("table.pk_duplicate_rate", {
          table_name: T,
          value: null,
          score: null,
          status: "not_evaluated",
          detail: { reason: "sampled mode cannot measure primary-key duplicates" },
        }),
      ],
      T,
    );
    expect(unit).toMatchObject({ state: "not_scored", note: "sampled mode cannot measure primary-key duplicates" });
    expect(unitsMean([unit!])).toBeNull();
  });
});
