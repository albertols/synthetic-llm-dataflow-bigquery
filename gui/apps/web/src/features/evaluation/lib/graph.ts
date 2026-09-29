/**
 * The model graph: tables as nodes (parents left, children right, by
 * foreign-key depth), foreign keys as edges from child to parent, each
 * carrying its orphan and fan-out rows. Built from the relationship model
 * when `/api/relationships` resolves it, and always from the metric rows'
 * `edge` labels (`parseEdge`), so a live model the BFF cannot read still
 * draws.
 */
import type { EvaluationDetail, MetricRow } from "@contracts/api";
import { findEdge, formatEdge, parseEdge, type RelationshipModel } from "@contracts/relational";

import { statusRank, worstStatus } from "./model";
import { isDocumentedEdge, onDocumentedEdge } from "./reading";

export interface GraphNode {
  name: string;
  external: boolean;
  role: string | null;
  score: number | null;
  status: string | null;
  layer: number;
  x: number;
  y: number;
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
  nodes: GraphNode[];
  edges: GraphEdge[];
  width: number;
  height: number;
}

export const NODE_W = 168;
export const NODE_H = 60;
const COL_GAP = 132;
const ROW_GAP = 28;
const PAD = 16;

function lastSegment(name: string): string {
  return name.split(".").at(-1) ?? name;
}

export function buildGraph(
  detail: Pick<EvaluationDetail, "evaluation" | "metrics">,
  model?: RelationshipModel | null,
): Graph {
  const { evaluation, metrics } = detail;
  const tables = new Map<string, { role: string | null }>();
  for (const t of evaluation.tables) if (t.name) tables.set(t.name, { role: t.role });
  for (const t of model?.tables ?? []) if (!tables.has(t.name)) tables.set(t.name, { role: null });
  for (const m of metrics)
    if (m.level !== "model" && !tables.has(m.table_name)) tables.set(m.table_name, { role: null });

  const resolve = (ref: string): string => {
    if (tables.has(ref)) return ref;
    const last = lastSegment(ref);
    if (tables.has(last)) return last;
    return ref;
  };

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
      documented: found ? !found.edge.enforced : false,
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
  for (const edge of edges.values()) {
    names.add(edge.child);
    names.add(edge.parent);
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
  for (const name of sorted) {
    const d = depthOf(name, new Set());
    const list = layers.get(d) ?? [];
    list.push(name);
    layers.set(d, list);
  }
  const scoreRows = new Map<string, MetricRow>();
  for (const row of metrics) if (row.metric_id === "table.overall_score") scoreRows.set(row.table_name, row);
  const nodes: GraphNode[] = [];
  const layerCount = Math.max(...layers.keys(), 0) + 1;
  const tallest = Math.max(...[...layers.values()].map((l) => l.length), 1);
  for (const [layer, list] of layers) {
    const offset = ((tallest - list.length) * (NODE_H + ROW_GAP)) / 2;
    list.forEach((name, index) => {
      const info = tables.get(name);
      const registry = evaluation.tables.find((t) => t.name === name);
      const scoreRow = scoreRows.get(name);
      nodes.push({
        name,
        external: !info || info.role === "external",
        role: info?.role ?? "external",
        score: scoreRow?.value ?? registry?.table_score ?? null,
        status: scoreRow?.status ?? null,
        layer,
        x: PAD + layer * (NODE_W + COL_GAP),
        y: PAD + offset + index * (NODE_H + ROW_GAP),
      });
    });
  }
  return {
    nodes,
    edges: [...edges.values()].sort(
      (a, b) => statusRank(a.status) - statusRank(b.status) || a.label.localeCompare(b.label),
    ),
    width: PAD * 2 + layerCount * NODE_W + (layerCount - 1) * COL_GAP,
    height: PAD * 2 + tallest * NODE_H + (tallest - 1) * ROW_GAP,
  };
}
