/**
 * The model graph (SVG): tables left to right by foreign-key depth, a score
 * bar per node (length = table overall score, sequential colour), arrows from
 * child to parent marked with the edge's orphan / fan-out status as an icon
 * and a word, never colour alone. Documented edges are dashed and INFO.
 * Nodes are buttons: click (or Enter) scopes the run view to that table.
 */
import { useId, useMemo, useState, type KeyboardEvent } from "react";

import { InfoHint } from "@/components/InfoHint";
import { Button } from "@/components/ui/button";
import { formatPercent, MISSING } from "@/lib/format";

import { fmtMetric, fmtScore } from "../lib/format";
import { NODE_H, NODE_W, type Graph, type GraphEdge, type GraphNode } from "../lib/graph";
import { VisualFrame } from "../components/VisualFrame";

const STATUS_GLYPH: Record<string, string> = { pass: "✓", warn: "!", fail: "✕", info: "i", not_evaluated: "–" };
const STATUS_STROKE: Record<string, string> = {
  pass: "var(--status-good)",
  warn: "var(--status-warn)",
  fail: "var(--status-critical)",
  info: "var(--status-neutral)",
  not_evaluated: "var(--status-neutral)",
};

function seqForScore(score: number | null): string {
  if (score === null) return "var(--slate)";
  const step = Math.min(7, Math.max(2, Math.round(2 + score * 5)));
  return `var(--seq-${step})`;
}

function edgeWord(edge: GraphEdge): string {
  if (edge.documented) return "documented";
  switch (edge.status) {
    case "pass":
      return "ok";
    case "warn":
      return "warn";
    case "fail":
      return "fail";
    case null:
      return "no metrics";
    default:
      return edge.status;
  }
}

function EdgePath({ edge, nodes }: { edge: GraphEdge; nodes: Map<string, GraphNode> }) {
  const child = nodes.get(edge.child);
  const parent = nodes.get(edge.parent);
  if (!child || !parent) return null;
  const self = child === parent;
  const x1 = child.x;
  const y1 = child.y + NODE_H / 2;
  const x2 = parent.x + NODE_W;
  const y2 = parent.y + NODE_H / 2;
  const sameLayer = child.layer === parent.layer;
  const span = Math.abs(child.layer - parent.layer);
  let path: string;
  let mx: number;
  let my: number;
  if (self) {
    path = `M ${x1 + 20} ${child.y} C ${x1 + 20} ${child.y - 30}, ${x1 + 60} ${child.y - 30}, ${x1 + 60} ${child.y}`;
    mx = x1 + 40;
    my = child.y - 26;
  } else if (sameLayer) {
    path = `M ${x1} ${y1} C ${x1 - 40} ${y1}, ${x1 - 40} ${y2}, ${parent.x} ${y2}`;
    mx = x1 - 36;
    my = (y1 + y2) / 2;
  } else if (span > 1) {
    // Route long edges around the layers in between, above or below by where the parent sits.
    const yb = y2 <= y1 ? Math.min(y1, y2) - NODE_H * 0.9 : Math.max(y1, y2) + NODE_H * 0.9;
    path = `M ${x1} ${y1} C ${x1 - 60} ${yb}, ${x2 + 60} ${yb}, ${x2} ${y2}`;
    mx = (x1 + x2) / 2;
    my = 0.125 * (y1 + y2) + 0.75 * yb;
  } else {
    path = `M ${x1} ${y1} C ${x1 - 48} ${y1}, ${x2 + 48} ${y2}, ${x2} ${y2}`;
    mx = (x1 + x2) / 2;
    my = (y1 + y2) / 2;
  }
  const stroke = edge.documented ? "var(--status-neutral)" : (STATUS_STROKE[edge.status ?? ""] ?? "var(--slate)");
  const glyph = edge.documented ? "i" : (STATUS_GLYPH[edge.status ?? ""] ?? "·");
  const orphan = edge.orphan?.value;
  const label = `${glyph} ${edgeWord(edge)}${orphan !== undefined && orphan !== null ? ` · ${formatPercent(orphan, 2)} orphans` : ""}`;
  const labelWidth = Math.round(label.length * 5.8 + 16);
  return (
    <g data-edge={edge.label}>
      <path
        d={path}
        fill="none"
        stroke={stroke}
        strokeWidth={2}
        strokeDasharray={edge.documented ? "5 4" : undefined}
        markerEnd="url(#eval-graph-arrow)"
      />
      <rect
        x={mx - labelWidth / 2}
        y={my - 9}
        width={labelWidth}
        height={18}
        rx={9}
        fill="var(--surface-2)"
        stroke={stroke}
        strokeWidth={1}
      />
      <text x={mx} y={my + 4} textAnchor="middle" fontSize={10} fill="var(--text-1)">
        {label}
      </text>
    </g>
  );
}

function NodeBox({
  node,
  selected,
  onSelect,
}: {
  node: GraphNode;
  selected: boolean;
  onSelect: (name: string) => void;
}) {
  const [focused, setFocused] = useState(false);
  const onKeyDown = (event: KeyboardEvent<SVGGElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(node.name);
    }
  };
  const score = node.score;
  const barWidth = (NODE_W - 20) * Math.max(0, Math.min(1, score ?? 0));
  const glyph = STATUS_GLYPH[node.status ?? ""] ?? "";
  return (
    <g
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${node.name}${node.external ? " (external)" : ""}: overall score ${fmtScore(score)}${node.status ? `, ${node.status}` : ""}. ${selected ? "Showing only this table; press to show all tables." : "Press to scope the views to this table."}`}
      data-table={node.name}
      onClick={() => onSelect(node.name)}
      onKeyDown={onKeyDown}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      className="cursor-pointer outline-none"
    >
      {focused || selected ? (
        <rect
          x={node.x - 4}
          y={node.y - 4}
          width={NODE_W + 8}
          height={NODE_H + 8}
          rx={12}
          fill="none"
          stroke={focused ? "var(--focus-ring)" : "var(--accent)"}
          strokeWidth={2}
        />
      ) : null}
      <rect
        x={node.x}
        y={node.y}
        width={NODE_W}
        height={NODE_H}
        rx={9}
        fill="var(--surface-2)"
        stroke="var(--border-strong)"
        strokeDasharray={node.external ? "4 3" : undefined}
      />
      <text
        x={node.x + 10}
        y={node.y + 20}
        fontSize={12}
        fontWeight={600}
        fill="var(--text-1)"
        fontFamily="var(--font-code)"
      >
        {node.name.length > 20 ? `${node.name.slice(0, 19)}…` : node.name}
      </text>
      <text x={node.x + NODE_W - 10} y={node.y + 20} fontSize={11} textAnchor="end" fill="var(--text-2)">
        {node.external ? "external" : `${glyph} ${fmtScore(score)}`}
      </text>
      <rect x={node.x + 10} y={node.y + 34} width={NODE_W - 20} height={6} rx={3} fill="var(--surface-3)" />
      {score !== null ? (
        <rect x={node.x + 10} y={node.y + 34} width={barWidth} height={6} rx={3} fill={seqForScore(score)} />
      ) : null}
      <text x={node.x + 10} y={node.y + 53} fontSize={10} fill="var(--text-3)">
        {node.role ?? ""}
      </text>
    </g>
  );
}

export function ModelGraph({
  graph,
  modelName,
  resolvedFrom,
  selected,
  onSelect,
}: {
  graph: Graph;
  modelName: string | null;
  resolvedFrom: "model" | "edges";
  selected?: string;
  onSelect: (table: string | undefined) => void;
}) {
  const titleId = useId();
  const nodes = useMemo(() => new Map(graph.nodes.map((n) => [n.name, n])), [graph.nodes]);
  const data = useMemo(
    () => [
      ...graph.nodes.map((n) => ({
        kind: "table",
        name: n.name,
        role: n.role,
        overall_score: n.score,
        status: n.status,
      })),
      ...graph.edges.map((e) => ({
        kind: "edge",
        name: e.label,
        role: e.documented ? "documented" : e.role,
        orphan_rate: e.orphan?.value ?? null,
        source_orphan_rate: e.orphanSource?.value ?? null,
        fanout_tvd: e.fanout?.value ?? null,
        status: e.status,
      })),
    ],
    [graph],
  );
  const select = (name: string) => onSelect(selected === name ? undefined : name);
  return (
    <VisualFrame
      title={`Model graph${modelName ? ` · ${modelName}` : ""}`}
      concept="eval:model-graph"
      description={
        resolvedFrom === "model"
          ? "From the relationship model. Node bar = table overall score; arrows point from child to parent."
          : "Rebuilt from the metric rows' edge labels (the model itself is not readable here)."
      }
      data={data}
      actions={
        selected ? (
          <Button variant="ghost" size="sm" onClick={() => onSelect(undefined)}>
            Show all tables
          </Button>
        ) : null
      }
      empty={{ when: graph.nodes.length === 0, message: "No tables to draw." }}
      footer={
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-text-3">
          <span className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="inline-block h-1.5 w-10 rounded-full bg-gradient-to-r from-(--seq-2) to-(--seq-7)"
            />
            table score, low → high
          </span>
          <span>✓ ok · ! warn · ✕ fail · i documented (INFO)</span>
          <span className="inline-flex items-center gap-0.5">
            dashed arrow: documented edge <InfoHint concept="eval:documented-edge" />
          </span>
          <span>dashed box: external parent</span>
        </div>
      }
    >
      <div className="overflow-x-auto">
        <svg
          viewBox={`-8 -56 ${graph.width + 16} ${graph.height + 112}`}
          width="100%"
          style={{ minWidth: Math.min(graph.width, 520), maxHeight: 420 }}
          role="group"
          aria-labelledby={titleId}
        >
          <title id={titleId}>
            {`Relationship model with ${graph.nodes.length} tables and ${graph.edges.length} foreign keys. ${
              selected ? `Scoped to ${selected}.` : "No table selected."
            }`}
          </title>
          <defs>
            <marker
              id="eval-graph-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M0 0 L10 5 L0 10 z" fill="var(--text-2)" />
            </marker>
          </defs>
          {graph.edges.map((edge) => (
            <EdgePath key={edge.label} edge={edge} nodes={nodes} />
          ))}
          {graph.nodes.map((node) => (
            <NodeBox key={node.name} node={node} selected={selected === node.name} onSelect={select} />
          ))}
        </svg>
      </div>
      {graph.edges.length ? (
        <ul className="mt-3 grid gap-1.5 text-xs text-text-2" aria-label="Foreign keys">
          {graph.edges.map((edge) => (
            <li key={edge.label} className="grid min-w-0 grid-cols-[1rem_minmax(0,1fr)] gap-x-1.5">
              <span aria-hidden="true" className="text-text-1">
                {edge.documented ? "i" : (STATUS_GLYPH[edge.status ?? ""] ?? "·")}
              </span>
              <span className="font-mono break-all text-text-1">{edge.label}</span>
              <span className="col-start-2 text-text-3">
                {edge.documented ? "documented (INFO)" : edgeWord(edge)} · orphans{" "}
                {fmtMetric(edge.orphan?.value ?? null, "share")}
                {edge.fanout ? ` · fan-out TVD ${fmtMetric(edge.fanout.value, "distance")}` : ""}
                {edge.orphan === null && edge.fanout === null ? ` · ${MISSING}` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </VisualFrame>
  );
}
