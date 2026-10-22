/**
 * The model graph's layout (ADR 0036, ADR 0042): components of two or more tables keep today's layered
 * coordinates; a table with no foreign key leaves the layers and becomes a tile.
 */
import type { DeclaredTable } from "@contracts/record";
import type { RelationshipModel } from "@contracts/relational";
import { describe, expect, it } from "vitest";

import { evaluation, forestDetail, metric, tableEntry } from "../test/fixtures";
import { buildGraph, TILE_GRID_MAX_REM, TILE_ROWS_MAX, type Graph } from "./graph";

const layered = (g: Graph) =>
  Object.fromEntries(g.nodes.filter((n) => n.placement === "layer").map((n) => [n.name, [n.x, n.y]]));

/** users ──< orders ──< order_items >── products (external), plus `isolated` tables with no edge. */
function thelook(isolated: string[] = []) {
  return {
    evaluation: evaluation({
      tables: [
        tableEntry("users"),
        tableEntry("orders", { role: "driven" }),
        tableEntry("order_items", { role: "driven" }),
        tableEntry("products", { role: "external" }),
        ...isolated.map((name) => tableEntry(name, { role: "isolated" })),
      ],
    }),
    metrics: [
      metric("relationship.orphan_rate", {
        table_name: "orders",
        edge: "orders(user_id) -> users(id)",
        detail: { orphans: 0, enforced: true, role: "driving" },
      }),
      metric("relationship.orphan_rate", {
        table_name: "order_items",
        edge: "order_items(order_id) -> orders(id)",
        detail: { orphans: 0, enforced: true, role: "driving" },
      }),
      metric("relationship.orphan_rate", {
        table_name: "order_items",
        edge: "order_items(product_id) -> synthetic_data.products(id)",
        detail: { orphans: 0, enforced: true, role: "external" },
      }),
    ],
  };
}

const THELOOK_LAYOUT = { order_items: [616, 60], orders: [316, 60], products: [16, 16], users: [16, 104] };

const table = (name: string, over: Partial<DeclaredTable> = {}): DeclaredTable => ({
  name,
  pk: [],
  identity: [],
  fk: [],
  enabled: true,
  source_table: null,
  num_rows: null,
  ...over,
});

describe("buildGraph: components keep their layout, isolated tables become tiles (ADR 0036)", () => {
  it("lays a model with no isolated table out exactly as before", () => {
    const g = buildGraph(thelook());
    expect(layered(g)).toEqual(THELOOK_LAYOUT);
    expect([g.width, g.height]).toEqual([800, 180]);
    expect(g.tiles).toEqual([]);
    expect(g.resolvedFrom).toBe("edges");
  });

  it("takes degree-0 tables out of the layers: the component's coordinates do not move", () => {
    const g = buildGraph(thelook(["c_iso", "a_iso", "b_iso"]));
    expect(layered(g)).toEqual(THELOOK_LAYOUT);
    expect([g.width, g.height]).toEqual([800, 180]);
    expect(g.tiles.map((t) => [t.name, t.placement, t.x, t.y])).toEqual([
      ["a_iso", "tile", 0, 0],
      ["b_iso", "tile", 0, 0],
      ["c_iso", "tile", 0, 0],
    ]);
  });

  it("draws a forest of 120 tables as tiles only", () => {
    const g = buildGraph(forestDetail());
    expect(g.nodes.filter((n) => n.placement === "layer")).toEqual([]);
    expect([g.width, g.height]).toEqual([0, 0]);
    expect(g.tiles).toHaveLength(120);
  });

  // A constant pin, not a layout measurement: the rendered height is asserted in forest-view.test.tsx.
  it("the grid's bound is 12 rows, 46.125 rem", () => {
    expect(TILE_ROWS_MAX).toBe(12);
    expect(TILE_GRID_MAX_REM).toBe(46.125);
  });

  it("keeps a table that references itself in the layered drawing", () => {
    const g = buildGraph({
      evaluation: evaluation({ tables: [tableEntry("A_TABLE")] }),
      metrics: [
        metric("relationship.orphan_rate", { table_name: "A_TABLE", edge: "A_TABLE(COL_002) -> A_TABLE(COL_001)" }),
      ],
    });
    expect(g.nodes.map((n) => [n.name, n.placement])).toEqual([["A_TABLE", "layer"]]);
  });

  it("does not draw a disabled model table, nor an enabled one the evaluation does not cover", () => {
    const model: RelationshipModel = {
      model: "m",
      description: "",
      source: "",
      sha12: "",
      generation_order: [],
      tables: [
        { name: "A_TABLE", pk: ["COL_001"], identity: [], enabled: true, note: "", fk: [] },
        { name: "B_TABLE", pk: [], identity: [], enabled: false, note: "", fk: [] },
        { name: "C_TABLE", pk: [], identity: [], enabled: true, note: "", fk: [] },
      ],
    };
    const g = buildGraph(
      { evaluation: evaluation({ tables: [tableEntry("A_TABLE", { role: "isolated" })] }), metrics: [] },
      model,
    );
    // C_TABLE has no facts and no score: the model card counts it ("of N declared"), the grid does not.
    expect(g.nodes.map((n) => n.name)).toEqual(["A_TABLE"]);
    expect(g.resolvedFrom).toBe("model");
  });

  it("keeps an uncovered model table that has a foreign key in the layers", () => {
    const model: RelationshipModel = {
      model: "m",
      description: "",
      source: "",
      sha12: "",
      generation_order: [],
      tables: [
        { name: "A_TABLE", pk: ["COL_001"], identity: [], enabled: true, note: "", fk: [] },
        {
          name: "C_TABLE",
          pk: [],
          identity: [],
          enabled: true,
          note: "",
          fk: [
            {
              cols: ["COL_002"],
              ref: "A_TABLE",
              ref_cols: ["COL_001"],
              enforced: true,
              drives: true,
              external: false,
              role: "driving",
              drawn_cols: ["COL_002"],
              note: "",
            },
          ],
        },
      ],
    };
    const g = buildGraph({ evaluation: evaluation({ tables: [tableEntry("A_TABLE")] }), metrics: [] }, model);
    expect(g.nodes.map((n) => [n.name, n.placement])).toEqual([
      ["A_TABLE", "layer"],
      ["C_TABLE", "layer"],
    ]);
  });

  it("draws the launch's own declared model when no model is served (ADR 0044)", () => {
    const declared = [
      table("orders", {
        pk: ["id"],
        fk: [
          { cols: ["user_id"], ref: "users", ref_cols: ["id"], enforced: true },
          { cols: ["buyer_id"], ref: "users", ref_cols: ["id"], enforced: false },
        ],
      }),
      table("users", { pk: ["id"] }),
      table("logs"),
    ];
    const detail = {
      evaluation: evaluation({ tables: ["orders", "users", "logs"].map((n) => tableEntry(n)) }),
      metrics: [],
    };
    const g = buildGraph(detail, null, [], declared);
    expect(g.resolvedFrom).toBe("record");
    expect(g.edges.map((e) => [e.label, e.documented]).sort()).toEqual([
      ["orders(buyer_id) -> users(id)", true],
      ["orders(user_id) -> users(id)", false],
    ]);
    expect(g.tiles.map((t) => t.name)).toEqual(["logs"]);
    // A served model wins over the record.
    const model: RelationshipModel = {
      model: "m",
      description: "",
      source: "",
      sha12: "",
      generation_order: [],
      tables: [],
    };
    expect(buildGraph(detail, model, [], declared).resolvedFrom).toBe("model");
  });
});
