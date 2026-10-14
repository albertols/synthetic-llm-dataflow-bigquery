/**
 * StatTile — one number with its label (dataviz "stat tile" contract).
 *
 *   <StatTile label="Evaluations" value={40} concept="core:status" />
 *   <StatTile label="DKW band at n = 10,000" value={0.0136} format={(v) => formatFixed(v, 4)} unit="ε" />
 *   <StatTile label="Overall score" value={0.87} delta={{ value: 0.04, vs: "last run", goodWhen: "up" }}
 *             trend={[0.71, 0.74, 0.8, 0.83, 0.87]} />
 *
 * Proportional figures for the value; the delta is signed, with an arrow and
 * "vs <period>" in words, coloured good/bad only when `goodWhen` says which
 * way is good; the optional sparkline is decorative (its range is in text).
 */
import { ArrowDownRight, ArrowRight, ArrowUpRight } from "lucide-react";
import { useId, type ReactNode } from "react";

import { cn } from "@/lib/cn";
import { formatCompact, formatNumber, isFiniteNumber, MISSING } from "@/lib/format";

import { InfoHint } from "./InfoHint";

export type StatDelta = {
  value: number;
  /** What the delta compares against: "last run", "the 10k sample". */
  vs: string;
  /** Which direction is good; omit for a neutral delta. */
  goodWhen?: "up" | "down";
  format?: (value: number) => string;
};

export type StatTileProps = {
  label: string;
  value: number | string | null | undefined;
  unit?: string;
  /** Formats a numeric value; default: compact at ≥ 10,000 ("12.9M"), else up to 4 decimals. */
  format?: (value: number) => string;
  delta?: StatDelta;
  /** Concept id for the (i) next to the label. */
  concept?: string;
  /** Up to ~12 points, oldest first, drawn as a sparkline. */
  trend?: readonly number[];
  /** One line under the value (source, n, provenance). */
  footnote?: ReactNode;
  className?: string;
};

function defaultFormat(value: number): string {
  return Math.abs(value) >= 10_000 ? formatCompact(value) : formatNumber(value, 4);
}

function Sparkline({ points }: { points: readonly number[] }) {
  const finite = points.filter(isFiniteNumber);
  if (finite.length < 2) return null;
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const span = max - min || 1;
  const width = 96;
  const height = 28;
  const step = width / (finite.length - 1);
  const coords = finite.map((v, i) => [i * step, height - 3 - ((v - min) / span) * (height - 6)] as const);
  const last = coords[coords.length - 1]!;
  return (
    <svg
      viewBox={`-4 0 ${width + 8} ${height}`}
      width={width + 8}
      height={height}
      aria-hidden="true"
      className="shrink-0"
    >
      <polyline
        points={coords.map(([x, y]) => `${x},${y}`).join(" ")}
        fill="none"
        stroke="var(--slate)"
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={last[0]} cy={last[1]} r="3.5" fill="var(--accent)" stroke="var(--surface-1)" strokeWidth="2" />
    </svg>
  );
}

export function StatTile({
  label,
  value,
  unit,
  format = defaultFormat,
  delta,
  concept,
  trend,
  footnote,
  className,
}: StatTileProps) {
  const labelId = useId();
  const text = typeof value === "number" ? (isFiniteNumber(value) ? format(value) : MISSING) : (value ?? MISSING);
  const deltaText = delta ? (delta.format ?? ((v: number) => formatNumber(v, 3)))(Math.abs(delta.value)) : "";
  const direction = delta ? (delta.value > 0 ? "up" : delta.value < 0 ? "down" : "flat") : "flat";
  const DeltaIcon = direction === "up" ? ArrowUpRight : direction === "down" ? ArrowDownRight : ArrowRight;
  const deltaTone =
    !delta?.goodWhen || direction === "flat"
      ? "text-text-2"
      : direction === delta.goodWhen
        ? "text-status-good-text"
        : "text-status-critical-text";
  const finiteTrend = trend?.filter(isFiniteNumber) ?? [];

  return (
    <div
      role="group"
      aria-labelledby={labelId}
      data-slot="stat-tile"
      className={cn("flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-surface-1 p-4", className)}
    >
      <div className="flex items-center gap-1">
        <span id={labelId} className="text-sm text-text-2">
          {label}
        </span>
        {concept ? <InfoHint concept={concept} /> : null}
      </div>
      <div className="flex items-end justify-between gap-3">
        <p className="flex min-w-0 items-baseline gap-1.5">
          <span className="truncate text-3xl leading-none font-semibold tracking-tight text-text-1">{text}</span>
          {unit ? <span className="text-sm text-text-3">{unit}</span> : null}
        </p>
        {finiteTrend.length > 1 ? <Sparkline points={finiteTrend} /> : null}
      </div>
      {delta ? (
        <p className={cn("flex items-center gap-1 text-xs font-medium", deltaTone)}>
          <DeltaIcon className="size-3.5" aria-hidden="true" />
          <span>
            {direction === "down" ? "−" : direction === "up" ? "+" : "±"}
            {deltaText} vs {delta.vs}
          </span>
        </p>
      ) : null}
      {finiteTrend.length > 1 ? (
        <p className="sr-only">
          Trend over {finiteTrend.length} points, from {formatNumber(finiteTrend[0])} to{" "}
          {formatNumber(finiteTrend[finiteTrend.length - 1])}.
        </p>
      ) : null}
      {footnote ? <div className="text-xs text-text-3">{footnote}</div> : null}
    </div>
  );
}
