/**
 * IntervalPlot — a forest plot: one row per estimate, a dot for the point
 * value and a whisker for its 95 % interval, on one shared axis with the
 * reference and threshold lines drawn through every row. Log or linear.
 * An open upper bound (m_H = 0) is drawn as an arrow to the edge. Every
 * number is also in the row's text and in the "View data" table.
 */
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

import { StatusPill } from "@/components/StatusPill";
import { cn } from "@/lib/cn";

export interface IntervalRow {
  key: string;
  label: ReactNode;
  sublabel?: string;
  value: number | null;
  lo: number | null;
  /** null with a non-null `lo`: unbounded above. */
  hi: number | null;
  status: string;
  /** Text next to the status (e.g. "3.0× · ci_low 0.03"). */
  detail: string;
}

export interface RefLine {
  x: number;
  label: string;
  tone: "ref" | "warn" | "fail";
}

export type IntervalScale = { kind: "linear" | "log"; lo: number; hi: number };

function position(scale: IntervalScale, x: number): number {
  if (scale.kind === "log") {
    const clamp = Math.max(x, scale.lo);
    const p = (Math.log(clamp) - Math.log(scale.lo)) / (Math.log(scale.hi) - Math.log(scale.lo));
    return Math.min(100, Math.max(0, p * 100));
  }
  return Math.min(100, Math.max(0, ((x - scale.lo) / (scale.hi - scale.lo)) * 100));
}

const TONE: Record<RefLine["tone"], string> = {
  ref: "bg-text-3",
  warn: "bg-status-warn",
  fail: "bg-status-critical",
};

function Lines({ scale, lines }: { scale: IntervalScale; lines: readonly RefLine[] }) {
  return (
    <>
      {lines.map((line) => (
        <span
          key={`${line.tone}-${line.x}`}
          className={cn("absolute inset-y-0 w-0.5 -translate-x-1/2", TONE[line.tone], line.tone === "ref" && "w-px")}
          style={{ left: `${position(scale, line.x)}%` }}
        />
      ))}
    </>
  );
}

export function IntervalPlot({
  rows,
  scale,
  lines,
  ticks,
  formatTick,
  className,
}: {
  rows: readonly IntervalRow[];
  scale: IntervalScale;
  lines: readonly RefLine[];
  ticks: readonly number[];
  formatTick: (x: number) => string;
  className?: string;
}) {
  return (
    <div className={cn("grid gap-1", className)}>
      <div
        aria-hidden="true"
        className="grid grid-cols-[minmax(0,7.5rem)_1fr] gap-3 sm:grid-cols-[minmax(0,12rem)_1fr_minmax(0,17rem)]"
      >
        <span />
        <div className="relative h-5">
          {lines.map((line) => (
            <span
              key={`label-${line.tone}-${line.x}`}
              className="absolute top-0 -translate-x-1/2 text-[10px] whitespace-nowrap text-text-3"
              style={{ left: `${position(scale, line.x)}%` }}
            >
              {line.label}
            </span>
          ))}
        </div>
        <span className="hidden sm:block" />
      </div>
      {rows.map((row) => {
        const hasCi = row.lo !== null;
        const hiOpen = hasCi && row.hi === null;
        const from = row.lo === null ? null : position(scale, row.lo);
        const to = hiOpen ? 100 : row.hi === null ? null : position(scale, row.hi);
        return (
          <div
            key={row.key}
            className="grid grid-cols-[minmax(0,7.5rem)_1fr] items-center gap-3 border-t border-border py-1.5 sm:grid-cols-[minmax(0,12rem)_1fr_minmax(0,17rem)]"
          >
            <div className="min-w-0">
              <div className="truncate text-xs text-text-1">{row.label}</div>
              {row.sublabel ? <div className="truncate font-mono text-[10px] text-text-3">{row.sublabel}</div> : null}
            </div>
            <div aria-hidden="true" className="relative h-6">
              <span className="absolute inset-x-0 top-1/2 h-px bg-(--chart-grid)" />
              <Lines scale={scale} lines={lines} />
              {from !== null && to !== null ? (
                <span
                  className="absolute top-1/2 h-0.5 -translate-y-1/2 bg-text-1"
                  style={{ left: `${from}%`, width: `${Math.max(to - from, 0.5)}%` }}
                />
              ) : null}
              {hiOpen ? (
                <ChevronRight className="absolute top-1/2 right-[-7px] size-3.5 -translate-y-1/2 text-text-1" />
              ) : null}
              {row.value !== null && Number.isFinite(row.value) ? (
                <span
                  className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent ring-2 ring-surface-1"
                  style={{ left: `${position(scale, row.value)}%` }}
                />
              ) : null}
            </div>
            <div className="col-span-2 flex min-w-0 items-center gap-2 sm:col-span-1">
              <StatusPill status={row.status} size="sm" />
              <span className="truncate font-mono text-[11px] text-text-2">{row.detail}</span>
            </div>
          </div>
        );
      })}
      <div
        aria-hidden="true"
        className="grid grid-cols-[minmax(0,7.5rem)_1fr] gap-3 sm:grid-cols-[minmax(0,12rem)_1fr_minmax(0,17rem)]"
      >
        <span />
        <div className="relative h-4 border-t border-(--chart-axis)">
          {ticks.map((t) => (
            <span
              key={t}
              className="absolute top-0.5 -translate-x-1/2 text-[10px] text-text-3 tabular-nums"
              style={{ left: `${position(scale, t)}%` }}
            >
              {formatTick(t)}
            </span>
          ))}
        </div>
        <span className="hidden sm:block" />
      </div>
    </div>
  );
}
