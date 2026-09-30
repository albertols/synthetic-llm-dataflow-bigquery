/**
 * Axis domains for the metric bar and the interval plots. Everything is
 * clipped to a finite domain; marks beyond it are drawn at the edge with an
 * arrow (the number is always in the text next to the mark).
 */
import type { Reading } from "./reading";

export interface Domain {
  lo: number;
  hi: number;
}

const finite = (xs: (number | null | undefined)[]) =>
  xs.filter((x): x is number => typeof x === "number" && Number.isFinite(x));

/** The axis a metric bar needs to show its value, CI, baseline, noise band and thresholds. */
export function readingDomain(reading: Reading): Domain {
  const { value, ciLow, ciHigh, baseline, warn, fail, noiseFloor, noiseRef, direction, target } = reading;
  if (direction === "target") {
    const t = target ?? 1;
    const offsets = finite([value, ciLow, ciHigh, baseline].map((v) => (v === null ? null : Math.abs(v - t))));
    const span = Math.max(...finite([fail, warn, noiseFloor]), ...offsets, 0.05) * 1.25;
    return { lo: Math.max(t - span, 0), hi: t + span };
  }
  const floorEdge = noiseFloor === null ? null : noiseRef + (direction === "higher_better" ? -noiseFloor : noiseFloor);
  const points = finite([value, ciLow, ciHigh, baseline, warn, fail, floorEdge, noiseRef]);
  if (!points.length) return { lo: 0, hi: 1 };
  let lo = Math.min(0, ...points);
  let hi = Math.max(...points);
  // Keep the thresholds readable next to a huge value: cap at 3× the fail threshold.
  const cap = fail !== null && fail > 0 && direction === "lower_better" ? fail * 3 : null;
  if (cap !== null && hi > cap) hi = Math.max(cap, fail! * 1.2);
  if (direction === "higher_better") {
    lo = Math.min(...points);
    const top = Math.max(...points, 1);
    hi = top;
    const pad = (hi - lo) * 0.12 || 0.05;
    lo = Math.max(0, lo - pad);
  } else {
    hi = hi + (hi - lo) * 0.12 || 1;
  }
  if (hi - lo < 1e-12) hi = lo + 1;
  return { lo, hi };
}

/** Position in percent of the domain, clamped to [0, 100]. */
export function pct(domain: Domain, x: number): number {
  const p = ((x - domain.lo) / (domain.hi - domain.lo)) * 100;
  return Math.min(100, Math.max(0, p));
}

export function outside(domain: Domain, x: number): "below" | "above" | null {
  if (x < domain.lo) return "below";
  if (x > domain.hi) return "above";
  return null;
}

/** "Nice" ticks for a linear axis (1, 2, 5 × 10^k steps). */
export function niceTicks(domain: Domain, count = 4): number[] {
  const span = domain.hi - domain.lo;
  if (!(span > 0)) return [domain.lo];
  const raw = span / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? raw;
  const first = Math.ceil(domain.lo / step) * step;
  const ticks: number[] = [];
  for (let t = first; t <= domain.hi + step * 1e-9; t += step) ticks.push(Number(t.toPrecision(12)));
  return ticks;
}
