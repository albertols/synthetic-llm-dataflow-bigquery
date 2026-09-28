/**
 * MetricBar — one metric on its own axis: the noise-floor band around the
 * noise reference, the warn and fail ticks (both sides for target metrics),
 * the reference baseline (diamond), the value (dot) and its CI whisker, and
 * the gate marker when the status reads a CI bound.
 *
 * Drawn with positioned HTML (percentages), so it stays crisp at any width.
 * Decorative for assistive tech: every number it draws is in the readout
 * text next to it (MetricReadout) — the bar never carries a value alone.
 */
import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/cn";

import type { Reading } from "../lib/reading";
import { outside, pct, readingDomain, type Domain } from "../lib/scale";

function Tick({ domain, x, tone, tall }: { domain: Domain; x: number | null; tone: "warn" | "fail"; tall?: boolean }) {
  if (x === null || outside(domain, x)) return null;
  return (
    <span
      className={cn(
        "absolute w-0.5 -translate-x-1/2 rounded-full",
        tall ? "top-0.5 bottom-0.5" : "top-1.5 bottom-1.5",
        tone === "warn" ? "bg-status-warn" : "bg-status-critical",
      )}
      style={{ left: `${pct(domain, x)}%` }}
    />
  );
}

export function MetricBar({
  reading,
  className,
  size = "md",
}: {
  reading: Reading;
  className?: string;
  size?: "sm" | "md";
}) {
  const domain = readingDomain(reading);
  const { value, ciLow, ciHigh, baseline, noiseFloor, noiseRef, warn, fail, direction, target } = reading;
  const band =
    noiseFloor === null ? null : { from: pct(domain, noiseRef - noiseFloor), to: pct(domain, noiseRef + noiseFloor) };
  const t = target ?? 1;
  const lowWhisker = ciLow ?? null;
  const highWhisker = ciHigh === null && ciLow !== null ? Number.POSITIVE_INFINITY : ciHigh;
  const valueOut = value === null ? null : outside(domain, value);
  return (
    <div
      aria-hidden="true"
      data-slot="metric-bar"
      className={cn("relative w-full min-w-24", size === "sm" ? "h-5" : "h-7", className)}
    >
      <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-chart-axis" />
      {band && band.to - band.from > 0 ? (
        <span
          className="absolute top-1 bottom-1 rounded-sm bg-slate/35"
          style={{ left: `${band.from}%`, width: `${Math.max(band.to - band.from, 0.8)}%` }}
        />
      ) : null}
      {direction === "target" ? (
        <>
          <Tick domain={domain} x={warn === null ? null : t - warn} tone="warn" />
          <Tick domain={domain} x={warn === null ? null : t + warn} tone="warn" />
          <Tick domain={domain} x={fail === null ? null : t - fail} tone="fail" tall />
          <Tick domain={domain} x={fail === null ? null : t + fail} tone="fail" tall />
          <span
            className="absolute top-1 bottom-1 w-px -translate-x-1/2 bg-text-3"
            style={{ left: `${pct(domain, t)}%` }}
          />
        </>
      ) : (
        <>
          <Tick domain={domain} x={warn} tone="warn" />
          <Tick domain={domain} x={fail} tone="fail" tall />
        </>
      )}
      {lowWhisker !== null && highWhisker !== null ? (
        <span
          className="absolute top-1/2 h-0.5 -translate-y-1/2 bg-text-1"
          style={{
            left: `${pct(domain, lowWhisker)}%`,
            width: `${Math.max(pct(domain, Math.min(highWhisker, domain.hi)) - pct(domain, lowWhisker), 0.5)}%`,
          }}
        />
      ) : null}
      {highWhisker !== null && highWhisker > domain.hi ? (
        <ChevronRight className="absolute top-1/2 right-[-6px] size-3.5 -translate-y-1/2 text-text-1" />
      ) : null}
      {baseline !== null && !outside(domain, baseline) ? (
        <span
          className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 bg-text-2 ring-2 ring-surface-1"
          style={{ left: `${pct(domain, baseline)}%` }}
        />
      ) : null}
      {reading.gateSource !== "value" && reading.gate !== null && !outside(domain, reading.gate) ? (
        <span
          className="absolute bottom-0 size-0 -translate-x-1/2 border-x-[5px] border-b-[7px] border-x-transparent border-b-text-1"
          style={{ left: `${pct(domain, reading.gate)}%` }}
        />
      ) : null}
      {value !== null ? (
        <span
          className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent ring-2 ring-surface-1"
          style={{ left: `${pct(domain, value)}%` }}
        />
      ) : null}
      {valueOut === "above" ? (
        <ChevronRight className="absolute top-1/2 right-[-8px] size-4 -translate-y-1/2 text-accent" />
      ) : valueOut === "below" ? (
        <ChevronLeft className="absolute top-1/2 left-[-8px] size-4 -translate-y-1/2 text-accent" />
      ) : null}
    </div>
  );
}
