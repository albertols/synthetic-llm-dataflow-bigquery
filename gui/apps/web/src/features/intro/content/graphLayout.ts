/**
 * A tiny layered layout for relationship graphs of a handful of tables:
 * parents above children (a table's layer is one below its deepest parent),
 * nodes spread evenly per layer, children ordered under their parents
 * (barycenter), and an edge that skips a layer bent sideways so it never
 * crosses the table in between. Pure: the SVG renderer only draws it.
 */
import type { EdgeRole, RelationshipModel } from "@contracts/relational";

export type GraphNode = {
  id: string;
  label: string;
  pk: string[];
  /** A dataset-qualified parent outside the model (never generated). */
  external?: boolean;
  /** Declared `enabled: false`. */
  disabled?: boolean;
};

export type GraphEdge = {
  /** The child table (the edge is declared on it). */
  from: string;
  /** The parent it references. */
  to: string;
  role: EdgeRole;
  cols: string[];
  refCols: string[];
  /** 1:1: the child's primary key is exactly the edge's columns. */
  oneToOne?: boolean;
  note?: string;
};

export type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };

export type PlacedNode = GraphNode & { x: number; y: number; width: number; height: number; layer: number };

export type PlacedEdge = GraphEdge & {
  /** SVG path from the child's top to the parent's bottom. */
  d: string;
  /** Where the role label sits. */
  label: { x: number; y: number };
  curved: boolean;
};

export type Layout = { width: number; height: number; nodes: PlacedNode[]; edges: PlacedEdge[] };

export const LAYOUT = {
  width: 320,
  layerGap: 70,
  padding: 14,
  nodeHeight: 32,
  charWidth: 6.4,
  minNodeWidth: 58,
  maxNodeWidth: 132,
  bend: 70,
  maxBend: 128,
} as const;

/** Nodes and edges of `model`, limited to `tables` (external parents come along as external nodes). */
export function graphFromModel(model: RelationshipModel, tables?: readonly string[]): Graph {
  const keep = new Set(tables ?? model.tables.map((t) => t.name));
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const seen = new Set<string>();
  const addNode = (node: GraphNode) => {
    if (seen.has(node.id)) return;
    seen.add(node.id);
    nodes.push(node);
  };
  for (const table of model.tables) {
    if (!keep.has(table.name)) continue;
    addNode({ id: table.name, label: table.name, pk: table.pk, disabled: !table.enabled });
  }
  for (const table of model.tables) {
    if (!keep.has(table.name)) continue;
    for (const fk of table.fk) {
      if (fk.external)
        addNode({ id: fk.ref, label: fk.ref.split(".").pop() ?? fk.ref, pk: fk.ref_cols, external: true });
      else if (!keep.has(fk.ref)) continue;
      const pk = [...table.pk].sort().join(",");
      edges.push({
        from: table.name,
        to: fk.ref,
        role: fk.role,
        cols: fk.cols,
        refCols: fk.ref_cols,
        oneToOne: pk === [...fk.cols].sort().join(","),
        note: fk.note || undefined,
      });
    }
  }
  return { nodes, edges };
}

function nodeWidth(node: GraphNode): number {
  const text = Math.max(node.label.length, node.pk.length ? `pk ${node.pk.join(",")}`.length * 0.85 : 0);
  return Math.min(LAYOUT.maxNodeWidth, Math.max(LAYOUT.minNodeWidth, text * LAYOUT.charWidth + 18));
}

/** Layer per node: 0 for roots and external parents, else one below the deepest parent. */
export function assignLayers(graph: Graph): Map<string, number> {
  const parents = new Map<string, string[]>();
  for (const edge of graph.edges) parents.set(edge.from, [...(parents.get(edge.from) ?? []), edge.to]);
  const layers = new Map<string, number>();
  const visiting = new Set<string>();
  const visit = (id: string): number => {
    const known = layers.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0; // a cycle cannot be declared, but never loop on one
    visiting.add(id);
    const ups = (parents.get(id) ?? []).filter((p) => p !== id);
    const layer = ups.length ? 1 + Math.max(...ups.map(visit)) : 0;
    visiting.delete(id);
    layers.set(id, layer);
    return layer;
  };
  for (const node of graph.nodes) visit(node.id);
  return layers;
}

export function layoutGraph(graph: Graph, width: number = LAYOUT.width): Layout {
  const layers = assignLayers(graph);
  const depth = Math.max(0, ...layers.values());
  // Deep graphs get tighter layers so a four-level chain keeps a readable width.
  const layerGap = depth >= 3 ? LAYOUT.layerGap - 14 : LAYOUT.layerGap;
  const byLayer: GraphNode[][] = Array.from({ length: depth + 1 }, () => []);
  for (const node of graph.nodes) byLayer[layers.get(node.id) ?? 0]!.push(node);

  const xOf = new Map<string, number>();
  const placed: PlacedNode[] = [];
  byLayer.forEach((row, layer) => {
    // Children sit under the mean x of their parents (declaration order breaks ties).
    const ordered =
      layer === 0 ? row : [...row].sort((a, b) => barycenter(a.id, graph, xOf) - barycenter(b.id, graph, xOf));
    const inner = width - 2 * LAYOUT.padding;
    ordered.forEach((node, index) => {
      const x = LAYOUT.padding + ((index + 0.5) / ordered.length) * inner;
      const y = LAYOUT.padding + layer * layerGap;
      xOf.set(node.id, x);
      placed.push({ ...node, x, y, width: nodeWidth(node), height: LAYOUT.nodeHeight, layer });
    });
  });

  const at = new Map(placed.map((node) => [node.id, node]));
  const edges: PlacedEdge[] = [];
  for (const edge of graph.edges) {
    const child = at.get(edge.from);
    const parent = at.get(edge.to);
    if (!child || !parent) continue;
    const x1 = child.x;
    const y1 = child.y;
    const x2 = parent.x;
    const y2 = parent.y + parent.height;
    const skips = child.layer - parent.layer > 1;
    const blocked = !skips && placed.some((n) => n !== child && n !== parent && crosses(n, x1, y1, x2, y2));
    if (skips || blocked) {
      const side = x2 < x1 ? -1 : x2 > x1 ? 1 : edgeSide(edge, graph);
      // Clear the widest table the edge passes (a cubic's midpoint sits at 3/4 of its control offset).
      const between = placed.filter((n) => n.layer > parent.layer && n.layer < child.layer);
      const mid = (x1 + x2) / 2;
      const needed = between.map((n) => (34 + n.width / 2 - side * (mid - n.x)) / 0.75);
      const bend = side * Math.min(LAYOUT.maxBend, Math.max(LAYOUT.bend, ...needed));
      const c1 = { x: x1 + bend, y: y1 - layerGap * 0.35 };
      const c2 = { x: x2 + bend, y: y2 + layerGap * 0.35 };
      const labelAt = cubicPoint({ x: x1, y: y1 }, c1, c2, { x: x2, y: y2 }, 0.5);
      edges.push({
        ...edge,
        d: `M ${r(x1)} ${r(y1)} C ${r(c1.x)} ${r(c1.y)}, ${r(c2.x)} ${r(c2.y)}, ${r(x2)} ${r(y2)}`,
        label: { x: labelAt.x, y: labelAt.y },
        curved: true,
      });
    } else {
      edges.push({
        ...edge,
        d: `M ${r(x1)} ${r(y1)} L ${r(x2)} ${r(y2)}`,
        label: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 },
        curved: false,
      });
    }
  }
  const height = LAYOUT.padding * 2 + depth * layerGap + LAYOUT.nodeHeight;
  return { width, height, nodes: placed, edges };
}

function barycenter(id: string, graph: Graph, xOf: Map<string, number>): number {
  const xs = graph.edges.filter((e) => e.from === id).map((e) => xOf.get(e.to) ?? 0);
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
}

/** Alternate sides for edges that leave one child straight up, so two bends never overlap. */
function edgeSide(edge: GraphEdge, graph: Graph): 1 | -1 {
  const siblings = graph.edges.filter((e) => e.from === edge.from);
  return siblings.indexOf(edge) % 2 === 0 ? -1 : 1;
}

/** Whether the segment (x1,y1)–(x2,y2) passes through a node's box. */
function crosses(node: PlacedNode, x1: number, y1: number, x2: number, y2: number): boolean {
  const top = node.y;
  const bottom = node.y + node.height;
  if (Math.max(y1, y2) < top || Math.min(y1, y2) > bottom) return false;
  const yMid = (top + bottom) / 2;
  const t = y1 === y2 ? 0 : (yMid - y1) / (y2 - y1);
  if (t <= 0 || t >= 1) return false;
  const x = x1 + t * (x2 - x1);
  return Math.abs(x - node.x) < node.width / 2;
}

function cubicPoint(
  p0: { x: number; y: number },
  p1: { x: number; y: number },
  p2: { x: number; y: number },
  p3: { x: number; y: number },
  t: number,
) {
  const u = 1 - t;
  const b0 = u * u * u;
  const b1 = 3 * u * u * t;
  const b2 = 3 * u * t * t;
  const b3 = t * t * t;
  return { x: b0 * p0.x + b1 * p1.x + b2 * p2.x + b3 * p3.x, y: b0 * p0.y + b1 * p1.y + b2 * p2.y + b3 * p3.y };
}

const r = (value: number) => Math.round(value * 10) / 10;
