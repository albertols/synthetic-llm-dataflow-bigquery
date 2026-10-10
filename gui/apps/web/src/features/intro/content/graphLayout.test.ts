import { describe, expect, it } from "vitest";

import { relationships } from "@contracts/generated/relationships";

import { assignLayers, graphFromModel, layoutGraph } from "./graphLayout";
import { SHAPES, shapeGraph } from "./shapes";

const model = (name: string) => relationships.models.find((m) => m.model === name)!;

describe("graphFromModel", () => {
  it("reads thelook's roles from the exported model: driving, implied, and an external parent", () => {
    const graph = graphFromModel(model("gcp_public_thelook"), ["users", "orders", "order_items"]);
    expect(graph.nodes.map((n) => n.id)).toEqual(["users", "orders", "order_items", "synthetic_data.products"]);
    const external = graph.nodes.find((n) => n.external)!;
    expect(external.label).toBe("products");
    expect(graph.edges.map((e) => `${e.from}->${e.to}:${e.role}`)).toEqual([
      "orders->users:driving",
      "order_items->orders:driving",
      "order_items->users:implied",
      "order_items->synthetic_data.products:external",
    ]);
  });

  it("keeps a documented edge and a disabled table of the chain", () => {
    const graph = graphFromModel(model("example_retail"), ["A_TABLE", "B_TABLE", "C_TABLE", "D_TABLE"]);
    expect(graph.nodes.find((n) => n.id === "D_TABLE")?.disabled).toBe(true);
    expect(graph.edges.map((e) => e.role)).toEqual(["driving", "driving", "documented", "disabled"]);
  });

  it("drops edges to parents outside the requested tables", () => {
    const graph = graphFromModel(model("example_star_diamond"), ["top", "left"]);
    expect(graph.edges).toHaveLength(1);
  });
});

describe("layoutGraph", () => {
  it("puts parents above children and bends an edge that skips a layer", () => {
    const graph = graphFromModel(model("gcp_public_thelook"), ["users", "orders", "order_items"]);
    const layers = assignLayers(graph);
    expect(Object.fromEntries(layers)).toEqual({
      users: 0,
      orders: 1,
      order_items: 2,
      "synthetic_data.products": 0,
    });
    const layout = layoutGraph(graph);
    const implied = layout.edges.find((e) => e.role === "implied")!;
    const driving = layout.edges.find((e) => e.from === "orders")!;
    expect(implied.curved).toBe(true);
    expect(implied.d).toMatch(/^M [\d.]+ [\d.]+ C /);
    expect(driving.curved).toBe(false);
    for (const node of layout.nodes) {
      expect(node.x - node.width / 2).toBeGreaterThanOrEqual(0);
      expect(node.x + node.width / 2).toBeLessThanOrEqual(layout.width);
    }
  });

  it("flags a 1:1 edge (the child's key is the edge) and lays out every gallery shape", () => {
    const forest = shapeGraph(
      SHAPES.find((s) => s.id === "forest")!,
      relationships.models,
    )!;
    expect(forest.edges.find((e) => e.from === "f")?.oneToOne).toBe(true);
    expect(forest.edges.find((e) => e.from === "b")?.oneToOne).toBe(false);
    for (const spec of SHAPES) {
      const graph = shapeGraph(spec, relationships.models);
      expect(graph, spec.id).not.toBeNull();
      const layout = layoutGraph(graph!);
      expect(layout.edges).toHaveLength(graph!.edges.length);
      expect(layout.height).toBeGreaterThan(0);
    }
  });
});
