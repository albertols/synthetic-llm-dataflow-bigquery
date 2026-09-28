/**
 * The relationship-shapes gallery: star, diamond, tree, chain, forest and
 * graph. Where a committed sample model (config/relationships/*.yaml, exported
 * to the generated contracts with every edge's role) contains the shape, the
 * card is drawn from that model's tables. The README's shape sweep supplies
 * the two shapes no committed model holds (a branching tree, a forest with a
 * 1:1 component); the registry test pins every one of them.
 */
import type { EdgeRole, RelationshipModel } from "@contracts/relational";

import type { Graph } from "./graphLayout";
import { graphFromModel } from "./graphLayout";

/** DESIGN.md §4.2, verbatim (links stripped): the gallery's lead. */
export const SHAPES_LEAD =
  "Stars, diamonds, trees, forests, 1:1 chains and a child that reaches both its parent and its grandparent all generate from one declared model (ADR 0037).";

export type ShapeId = "star" | "diamond" | "tree" | "chain" | "forest" | "graph";

export type ShapeSource =
  { kind: "model"; model: string; tables: readonly string[] } | { kind: "readme"; graph: Graph };

export type ShapeSpec = {
  id: ShapeId;
  title: string;
  /** From the README's shape section or DESIGN.md §4.2/§8 (verbatim where quoted). */
  caption: string;
  concept: `intro:shape-${ShapeId}`;
  source: ShapeSource;
};

const d = (from: string, to: string, role: EdgeRole, cols: string[], refCols = cols, oneToOne = false) => ({
  from,
  to,
  role,
  cols,
  refCols,
  oneToOne,
});

export const SHAPES: readonly ShapeSpec[] = [
  {
    id: "graph",
    title: "Graph",
    caption:
      "A child reaching its parent and its grandparent, plus a parent outside the model: a parent the driving parent already reaches is implied and costs nothing, and an external parent is a dataset-qualified table that is already landed.",
    concept: "intro:shape-graph",
    source: { kind: "model", model: "gcp_public_thelook", tables: ["users", "orders", "order_items"] },
  },
  {
    id: "star",
    title: "Star",
    caption:
      "A fact under two unrelated dimensions: a second parent with no column in common with the driving edge is independent, drawn as a whole key tuple from the sampled pool.",
    concept: "intro:shape-star",
    source: { kind: "model", model: "example_star_diamond", tables: ["dim_a", "dim_b", "fact"] },
  },
  {
    id: "diamond",
    title: "Diamond",
    caption:
      "Two branches rejoining: a second parent sharing a column is conditional — T comes from the driving key, R is a candidate that exists in right for that T.",
    concept: "intro:shape-diamond",
    source: { kind: "model", model: "example_star_diamond", tables: ["top", "left", "right", "bottom"] },
  },
  {
    id: "tree",
    title: "Tree",
    caption: "One parent per child is the common case and needs no flags at all.",
    concept: "intro:shape-tree",
    source: {
      kind: "readme",
      graph: {
        nodes: [
          { id: "root", label: "root", pk: ["R"] },
          { id: "mid", label: "mid", pk: ["R", "M"] },
          { id: "leaf1", label: "leaf1", pk: ["R", "M", "L"] },
          { id: "leaf2", label: "leaf2", pk: ["R", "M", "Q"] },
        ],
        edges: [
          d("mid", "root", "driving", ["R"]),
          d("leaf1", "mid", "driving", ["R", "M"]),
          d("leaf2", "mid", "driving", ["R", "M"]),
        ],
      },
    },
  },
  {
    id: "chain",
    title: "Chain",
    caption:
      "`enabled: false` on a table removes it from the graph, with anything that reached the model only through it. `enforced: false` on an edge keeps the table and stops drawing keys through that edge.",
    concept: "intro:shape-chain",
    source: { kind: "model", model: "example_retail", tables: ["A_TABLE", "B_TABLE", "C_TABLE", "D_TABLE"] },
  },
  {
    id: "forest",
    title: "Forest",
    caption: "Independent components, one of them 1:1: the PK IS the edge.",
    concept: "intro:shape-forest",
    source: {
      kind: "readme",
      graph: {
        nodes: [
          { id: "a", label: "a", pk: ["A"] },
          { id: "e", label: "e", pk: ["E"] },
          { id: "b", label: "b", pk: ["A", "B"] },
          { id: "f", label: "f", pk: ["E"] },
        ],
        edges: [d("b", "a", "driving", ["A"]), d("f", "e", "driving", ["E"], ["E"], true)],
      },
    },
  },
];

/** The graph a shape draws, from `models` (the API's list or the committed contracts). */
export function shapeGraph(spec: ShapeSpec, models: readonly RelationshipModel[]): Graph | null {
  if (spec.source.kind === "readme") return spec.source.graph;
  const { model: name, tables } = spec.source;
  const model = models.find((m) => m.model === name);
  return model ? graphFromModel(model, tables) : null;
}

/** Edge roles in the order the legend explains them. */
export const ROLE_ORDER: readonly EdgeRole[] = [
  "driving",
  "implied",
  "independent",
  "conditional",
  "external",
  "documented",
  "disabled",
];

export const ROLE_STYLE: Record<EdgeRole, { stroke: string; width: number; dash?: string; label: string }> = {
  driving: { stroke: "var(--accent)", width: 2.25, label: "drives" },
  implied: { stroke: "var(--text-2)", width: 1.6, dash: "1.5 3.5", label: "implied" },
  independent: { stroke: "var(--chart-1)", width: 1.8, dash: "7 4", label: "independent" },
  conditional: { stroke: "var(--chart-5)", width: 1.8, dash: "7 3 1.5 3", label: "conditional" },
  external: { stroke: "var(--text-2)", width: 1.6, label: "external" },
  documented: { stroke: "var(--slate)", width: 1.4, dash: "3 3", label: "documented" },
  disabled: { stroke: "var(--text-3)", width: 1.2, dash: "1 3", label: "disabled" },
};
