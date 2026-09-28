/**
 * One relationship graph as SVG: tables as boxes (parents above), foreign-key
 * edges from child to parent, each edge's role written on it and drawn with
 * its own dash pattern, so the role never rests on colour alone.
 */
import { useId, useMemo } from "react";

import type { EdgeRole } from "@contracts/relational";

import { cn } from "@/lib/cn";

import { layoutGraph, nodeCaption, type Graph, type PlacedNode } from "../content/graphLayout";
import { ROLE_ORDER, ROLE_STYLE } from "../content/shapes";

export function describeGraph(graph: Graph): string {
  const edges = graph.edges.map((e) => `${e.from} to ${e.to}, ${e.role}${e.oneToOne ? " (1:1)" : ""}`);
  return `${graph.nodes.length} tables: ${graph.nodes.map((n) => n.label + (n.external ? " (external)" : n.disabled ? " (disabled)" : "")).join(", ")}. Edges, child to parent: ${edges.join("; ") || "none"}.`;
}

export function RelationGraph({
  graph,
  title,
  highlight = false,
  className,
}: {
  graph: Graph;
  title: string;
  highlight?: boolean;
  className?: string;
}) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const layout = useMemo(() => layoutGraph(graph), [graph]);
  const roles = [...new Set(layout.edges.map((e) => e.role))];
  return (
    <svg
      viewBox={`0 0 ${layout.width} ${layout.height}`}
      role="img"
      aria-labelledby={`${id}-t ${id}-d`}
      className={cn("h-auto w-full", className)}
    >
      <title id={`${id}-t`}>{title}</title>
      <desc id={`${id}-d`}>{describeGraph(graph)}</desc>
      <defs>
        {ROLE_ORDER.filter((role) => roles.includes(role)).map((role) => (
          <marker
            key={role}
            id={`${id}-${role}`}
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6"
            markerHeight="6"
            orient="auto"
          >
            <path d="M0,0 L8,4 L0,8 z" fill={ROLE_STYLE[role].stroke} />
          </marker>
        ))}
      </defs>
      {layout.edges.map((edge) => {
        const style = ROLE_STYLE[edge.role];
        return (
          <path
            key={`${edge.from}->${edge.to}:${edge.cols.join(",")}`}
            d={edge.d}
            fill="none"
            stroke={style.stroke}
            strokeWidth={style.width}
            strokeDasharray={style.dash}
            strokeLinecap="round"
            opacity={edge.role === "disabled" ? 0.7 : 1}
            markerEnd={`url(#${id}-${edge.role})`}
          />
        );
      })}
      {layout.nodes.map((node) => (
        <TableBox key={node.id} node={node} highlight={highlight} />
      ))}
      {layout.edges.map((edge) => (
        <EdgeLabel
          key={`label:${edge.from}->${edge.to}:${edge.cols.join(",")}`}
          x={edge.label.x}
          y={edge.label.y}
          role={edge.role}
          oneToOne={edge.oneToOne}
        />
      ))}
    </svg>
  );
}

function TableBox({ node, highlight }: { node: PlacedNode; highlight: boolean }) {
  const x = node.x - node.width / 2;
  const maxChars = Math.floor((node.width - 10) / 5.2);
  return (
    <g opacity={node.disabled ? 0.55 : 1}>
      <rect
        x={x}
        y={node.y}
        width={node.width}
        height={node.height}
        rx="6"
        fill={node.external ? "var(--surface-1)" : "var(--surface-3)"}
        stroke={highlight && !node.external ? "var(--accent)" : "var(--border-strong)"}
        strokeWidth={highlight && !node.external ? 1.5 : 1}
        strokeDasharray={node.external || node.disabled ? "4 3" : undefined}
      />
      <text
        x={node.x}
        y={node.y + 13}
        textAnchor="middle"
        fontSize="10.5"
        fontFamily="var(--font-code)"
        fill="var(--text-1)"
        fontStyle={node.external ? "italic" : undefined}
      >
        {truncate(node.label, Math.floor((node.width - 8) / 6.2))}
      </text>
      <text
        x={node.x}
        y={node.y + 25}
        textAnchor="middle"
        fontSize="8.5"
        fontFamily="var(--font-code)"
        fill="var(--text-3)"
      >
        {truncate(nodeCaption(node), maxChars)}
      </text>
    </g>
  );
}

function EdgeLabel({ x, y, role, oneToOne }: { x: number; y: number; role: EdgeRole; oneToOne?: boolean }) {
  const text = `${ROLE_STYLE[role].label}${oneToOne ? " · 1:1" : ""}`;
  const width = text.length * 5.1 + 8;
  return (
    <g>
      <rect x={x - width / 2} y={y - 7} width={width} height="13" rx="3" fill="var(--surface-1)" opacity="0.92" />
      <text x={x} y={y + 3} textAnchor="middle" fontSize="8.5" fill="var(--text-2)">
        {text}
      </text>
    </g>
  );
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, Math.max(1, max - 1))}…` : text;
}
