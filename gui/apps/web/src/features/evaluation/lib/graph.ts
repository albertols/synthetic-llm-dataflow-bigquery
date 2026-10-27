/**
 * The model graph: tables as nodes (parents left, children right, by
 * foreign-key depth), foreign keys as edges from child to parent, each
 * carrying its orphan and fan-out rows. Built from the relationship model
 * when `/api/relationships` resolves it, and always from the metric rows'
 * `edge` labels (`parseEdge`), so a live model the BFF cannot read still
 * draws. Without a served model the launch's generation record (ADR 0044)
 * supplies the declared tables and keys. A table with no foreign key at all
 * leaves the layers and becomes a tile (ADR 0036): a forest of 100 tables is
 * a grid, not one 8,800-unit column.
 */
import type { EvaluationDetail, MetricRow } from "@contracts/api";
import type { DeclaredTable } from "@contracts/record";
import { findEdge, formatEdge, parseEdge, type RelationshipModel } from "@contracts/relational";

import type { AdjustedReading, AdjustmentRow } from "./adjustment";
import { statusRank, worstStatus } from "./model";
import { isDocumentedEdge, onDocumentedEdge } from "./reading";

export interface GraphNode {
  name: string;
  external: boolean;
  role: string | null;
  score: number | null;
  status: string | null;
  /**
   * ADR 0038: the generator's reading of this table's declared key, when the launch adjusted it
   * (or when that cannot be read); null on a table whose key is graded as unique. Drawn as the
   * "PK adj." chip — the node's score and status stay the evaluator's.
   */
  adjustment: Pick<AdjustedReading, "kind" | "verdict" | "tone" | "label"> | null;
  layer: number;
  x: number;
  y: number;
  /**
   * "layer": the table has a foreign key (as child or parent, or to itself) and is drawn in the layered
   * SVG at x, y. "tile": degree 0, drawn in the isolated-tables grid; x and y are 0.
   */
  placement: "layer" | "tile";
}

export interface GraphEdge {
  label: string;
  child: string;
  parent: string;
  role: string | null;
  documented: boolean;
  orphan: MetricRow | null;
  orphanSource: MetricRow | null;
  fanout: MetricRow | null;
  rows: MetricRow[];
  /** Worst status of the edge's enforced metrics (documented edges: "info"); null when none ran. */
  status: string | null;
}

export interface Graph {
  /** Every node: the layered ones first, then the tiles. */
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Size of the layered drawing; 0 x 0 when every node is a tile. */
  width: number;
  height: number;
  /** The degree-0 nodes in grid order (generation order, then name). */
  tiles: GraphNode[];
  /** What drew the model: a served model, the launch's record (ADR 0044), or the metric rows' edge labels. */
  resolvedFrom: "model" | "record" | "edges";
}

export const NODE_W = 168;
export const NODE_H = 60;
const COL_GAP = 132;
const ROW_GAP = 28;
const PAD = 16;
/**
 * The isolated-tables grid (ADR 0036, about 12 rows of 10): tiles at least TILE_MIN_REM wide (about ten
 * per row at full width), TILE_H_REM tall, TILE_GAP_REM apart; the frame is at most TILE_ROWS_MAX rows
 * tall and scrolls beyond that.
 */
export const TILE_MIN_REM = 7;
export const TILE_H_REM = 3.5;
export const TILE_GAP_REM = 0.375;
export const TILE_ROWS_MAX = 12;
export const TILE_GRID_MAX_REM = TILE_ROWS_MAX * TILE_H_REM + (TILE_ROWS_MAX - 1) * TILE_GAP_REM;
/** From this many tiles the overview gives the model graph the full row (the grid needs the width). */
export const WIDE_GRAPH_MIN_TILES = 10;

function lastSegment(name: string): string {
  return name.split(".").at(-1) ?? name;
}

export function buildGraph(
  detail: Pick<EvaluationDetail, "evaluation" | "metrics">,
  model?: RelationshipModel | null,
  adjustments?: readonly AdjustmentRow[],
  declared?: readonly DeclaredTable[],
): Graph {
  const { evaluation, metrics } = detail;
  const readings = new Map((adjustments ?? []).map((a) => [a.table, a.reading]));
  // Without a served model, the launch's record says what it declared (ADR 0044); a served model wins.
  const recorded = model ? [] : (declared ?? []);
  const tables = new Map<string, { role: string | null }>();
  for (const t of evaluation.tables) if (t.name) tables.set(t.name, { role: t.role });
  // What the evaluation covers: a table with facts or a score. A model table outside it is not a tile (below).
  const covered = new Set(tables.keys());
  for (const m of metrics) if (m.level !== "model") covered.add(m.table_name);
  for (const t of model?.tables ?? []) if (!tables.has(t.name)) tables.set(t.name, { role: null });
  for (const t of recorded) if (!tables.has(t.name)) tables.set(t.name, { role: null });
  for (const m of metrics)
    if (m.level !== "model" && !tables.has(m.table_name)) tables.set(m.table_name, { role: null });

  const resolve = (ref: string): string => {
    if (tables.has(ref)) return ref;
    const last = lastSegment(ref);
    if (tables.has(last)) return last;
    return ref;
  };

  const recordedEnforced = new Map<string, boolean>();
  const edges = new Map<string, GraphEdge>();
  const ensureEdge = (label: string, fallbackChild: string): GraphEdge | null => {
    const existing = edges.get(label);
    if (existing) return existing;
    const parsed = parseEdge(label);
    if (!parsed) return null;
    const child = resolve(parsed.child ?? fallbackChild);
    const parent = resolve(parsed.parent);
    const found = model ? findEdge(model, label) : null;
    const edge: GraphEdge = {
      label,
      child,
      parent,
      role: found?.edge.role ?? null,
      documented: found ? !found.edge.enforced : recordedEnforced.get(label) === false,
      orphan: null,
      orphanSource: null,
      fanout: null,
      rows: [],
      status: null,
    };
    edges.set(label, edge);
    return edge;
  };

  for (const table of model?.tables ?? []) {
    for (const fk of table.fk) ensureEdge(formatEdge(table.name, fk), table.name);
  }
  for (const table of recorded) {
    for (const fk of table.fk) {
      const label = formatEdge(table.name, fk);
      recordedEnforced.set(label, fk.enforced);
      ensureEdge(label, table.name);
    }
  }
  for (const row of metrics) {
    if (row.level !== "relationship" || !row.edge) continue;
    const edge = ensureEdge(row.edge, row.table_name);
    if (!edge) continue;
    edge.rows.push(row);
    if (onDocumentedEdge(row)) edge.documented = true;
    if (row.metric_id === "relationship.orphan_rate") {
      edge.orphan = row;
      if (isDocumentedEdge(row)) edge.documented = true;
      const role = (row.detail as { role?: unknown } | null)?.role;
      if (typeof role === "string") edge.role ??= role;
    } else if (row.metric_id === "relationship.orphan_rate_source") edge.orphanSource = row;
    else if (row.metric_id === "relationship.fanout_tvd") edge.fanout = row;
  }
  // Ruling R42: on a documented edge only the orphan rate is INFO; its fan-out rows compare
  // children per parent with the source and stay graded, so the edge reads the worst of them.
  for (const edge of edges.values()) {
    const graded = edge.rows.filter(
      (r) => r.status !== "info" && !(edge.documented && r.metric_id === "relationship.orphan_rate"),
    );
    edge.status = graded.length ? worstStatus(graded.map((r) => r.status)) : edge.rows.length ? "info" : null;
  }

  // Nodes: every table plus every parent the edges name (external parents).
  const names = new Set<string>(tables.keys());
  // Degree 0 (no edge as child or parent): a tile, out of the layered layout (ADR 0036).
  const touched = new Set<string>();
  for (const edge of edges.values()) {
    names.add(edge.child);
    names.add(edge.parent);
    touched.add(edge.child);
    touched.add(edge.parent);
  }
  const parentsOf = new Map<string, Set<string>>();
  for (const edge of edges.values()) {
    if (edge.child === edge.parent) continue;
    const set = parentsOf.get(edge.child) ?? new Set();
    set.add(edge.parent);
    parentsOf.set(edge.child, set);
  }
  const depth = new Map<string, number>();
  const depthOf = (name: string, seen: Set<string>): number => {
    const known = depth.get(name);
    if (known !== undefined) return known;
    if (seen.has(name)) return 0; // a cycle: break it
    seen.add(name);
    const parents = [...(parentsOf.get(name) ?? [])];
    const d = parents.length ? 1 + Math.max(...parents.map((p) => depthOf(p, seen))) : 0;
    depth.set(name, d);
    return d;
  };
  const order = model?.generation_order ?? [];
  const sorted = [...names].sort((a, b) => {
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib) || a.localeCompare(b);
  });
  const layers = new Map<number, string[]>();
  const tileNames: string[] = [];
  for (const name of sorted) {
    if (!touched.has(name)) {
      // A table the evaluation does not cover has no facts and no score: not a tile (the card counts it).
      if (covered.has(name)) tileNames.push(name);
      continue;
    }
    const d = depthOf(name, new Set());
    const list = layers.get(d) ?? [];
    list.push(name);
    layers.set(d, list);
  }
  const scoreRows = new Map<string, MetricRow>();
  for (const row of metrics) if (row.metric_id === "table.overall_score") scoreRows.set(row.table_name, row);
  const nodeOf = (name: string, layer: number, x: number, y: number, placement: GraphNode["placement"]): GraphNode => {
    const info = tables.get(name);
    const registry = evaluation.tables.find((t) => t.name === name);
    const scoreRow = scoreRows.get(name);
    return {
      name,
      external: !info || info.role === "external",
      role: info?.role ?? "external",
      score: scoreRow?.value ?? registry?.table_score ?? null,
      status: scoreRow?.status ?? null,
      adjustment: readings.get(name) ?? null,
      layer,
      x,
      y,
      placement,
    };
  };
  const nodes: GraphNode[] = [];
  const layered = layers.size > 0;
  const layerCount = layered ? Math.max(...layers.keys()) + 1 : 0;
  const tallest = layered ? Math.max(...[...layers.values()].map((l) => l.length)) : 0;
  for (const [layer, list] of layers) {
    const offset = ((tallest - list.length) * (NODE_H + ROW_GAP)) / 2;
    list.forEach((name, index) => {
      nodes.push(
        nodeOf(name, layer, PAD + layer * (NODE_W + COL_GAP), PAD + offset + index * (NODE_H + ROW_GAP), "layer"),
      );
    });
  }
  const tiles = tileNames.map((name) => nodeOf(name, 0, 0, 0, "tile"));
  nodes.push(...tiles);
  return {
    nodes,
    edges: [...edges.values()].sort(
      (a, b) => statusRank(a.status) - statusRank(b.status) || a.label.localeCompare(b.label),
    ),
    width: layered ? PAD * 2 + layerCount * NODE_W + (layerCount - 1) * COL_GAP : 0,
    height: layered ? PAD * 2 + tallest * NODE_H + (tallest - 1) * ROW_GAP : 0,
    tiles,
    resolvedFrom: model ? "model" : recorded.length ? "record" : "edges",
  };
}
