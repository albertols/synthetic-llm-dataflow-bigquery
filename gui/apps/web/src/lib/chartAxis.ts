/**
 * Value axes for ECharts, built once for every chart instead of per chart:
 *
 * - `linearAxis`: a nice-number tick step (1, 2, 2.5, 5 × 10ⁿ) with explicit
 *   min / max / interval, and labels printed with exactly as many decimals as
 *   the step needs — so adjacent ticks never read the same ("0.90 0.90 0.90"
 *   on a 0.897–0.903 range becomes 0.897 0.898 …).
 * - `scoreAxis`: a 0–1 score on [floor, 1], never past 1, with the floor the
 *   compare radar uses (0.5, or lower when a score is below it).
 * - `logAxis`: whole decades for ratios ≥ 0 that span orders of magnitude
 *   (lifts); 0 is drawn at the axis floor and said so, null is left out.
 * - `thresholdMarkLines`: warn / fail lines whose labels never collide — two
 *   lines closer than a label's height share one label ("warn 2× · fail 5×").
 */

export type AxisUnit = "plain" | "share" | "ratio" | "bits" | "count" | "score" | "auc";

export interface LinearAxis {
  kind: "linear";
  min: number;
  max: number;
  interval: number;
  /** Fraction digits a tick label needs (in the unit's own scale: a share's percent digits). */
  decimals: number;
  format: (value: number) => string;
}

export interface LogAxis {
  kind: "log";
  min: number;
  max: number;
  format: (value: number) => string;
  /** A value as the axis draws it: 0 (and anything below) at the floor. */
  place: (value: number) => number;
}

export type ValueAxis = LinearAxis | LogAxis;

const STEPS = [1, 2, 2.5, 5, 10];

/** A 1 / 2 / 2.5 / 5 × 10ⁿ step giving about `ticks` intervals over [lo, hi]. */
export function niceStep(lo: number, hi: number, ticks = 5): number {
  const span = Math.abs(hi - lo);
  const raw = span > 0 ? span / Math.max(1, ticks) : Math.abs(hi) > 0 ? Math.abs(hi) / 10 : 1;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / magnitude;
  return (STEPS.find((s) => norm <= s + 1e-9) ?? 10) * magnitude;
}

/** Fraction digits that print `step` (and so every multiple of it) exactly: 0.25 → 2, 5 → 0, 1e-4 → 4. */
export function stepDecimals(step: number): number {
  for (let d = 0; d <= 15; d += 1) {
    const scaled = step * 10 ** d;
    if (Math.abs(scaled - Math.round(scaled)) <= 1e-9 * Math.max(1, scaled)) return d;
  }
  return 15;
}

function unitFormat(unit: AxisUnit, decimals: number): (value: number) => string {
  const fixed = (v: number, d: number) => {
    const text = v.toFixed(Math.max(0, d));
    return /^-0(\.0*)?$/.test(text) ? text.slice(1) : text;
  };
  switch (unit) {
    case "share":
      return (v) => `${fixed(v * 100, decimals - 2)}%`;
    case "ratio":
      return (v) => `${fixed(v, decimals)}×`;
    case "bits":
      return (v) => `${fixed(v, decimals)} bits`;
    case "count":
      return (v) => new Intl.NumberFormat("en-US", { maximumFractionDigits: Math.max(0, decimals) }).format(v);
    default:
      return (v) => fixed(v, decimals);
  }
}

/**
 * A linear axis over `values` (non-finite and null ignored) plus `include` (thresholds,
 * bands) that must stay on the axis, clamped to `clamp` when given.
 */
export function linearAxis(
  values: readonly (number | null | undefined)[],
  options: {
    unit?: AxisUnit;
    include?: readonly (number | null | undefined)[];
    clamp?: readonly [number | null, number | null];
    ticks?: number;
  } = {},
): LinearAxis {
  const finite = [...values, ...(options.include ?? [])].filter(
    (v): v is number => typeof v === "number" && Number.isFinite(v),
  );
  let lo = finite.length ? Math.min(...finite) : 0;
  let hi = finite.length ? Math.max(...finite) : 1;
  const [clampLo, clampHi] = options.clamp ?? [null, null];
  if (clampLo !== null) lo = Math.max(lo, clampLo);
  if (clampHi !== null) hi = Math.min(hi, clampHi);
  if (lo === hi) {
    // One value: pad by a step of its own magnitude so the axis has room and readable ticks.
    const pad = niceStep(0, Math.abs(lo) || 1, 4);
    lo -= pad;
    hi += pad;
  }
  const step = niceStep(lo, hi, options.ticks ?? 5);
  let min = Math.floor(lo / step + 1e-9) * step;
  let max = Math.ceil(hi / step - 1e-9) * step;
  if (clampLo !== null) min = Math.max(min, clampLo);
  if (clampHi !== null) max = Math.min(max, clampHi);
  const decimals = stepDecimals(step);
  const unit = options.unit ?? "plain";
  // A share prints percent: its digits are two fewer than the fraction's.
  const format = unitFormat(unit, decimals);
  return {
    kind: "linear",
    min: round(min, decimals + 2),
    max: round(max, decimals + 2),
    interval: step,
    decimals,
    format,
  };
}

const round = (v: number, digits: number) => Number(v.toFixed(Math.min(20, Math.max(0, digits))));

/**
 * The low end of a 0–1 score axis: 0.5, or lower when a score is below it —
 * floor(min × 10) / 10 — so 0.2 and 0.5 never draw at the same place.
 */
export function scoreAxisFloor(scores: readonly (number | null | undefined)[]): number {
  const finite = scores.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (!finite.length) return 0.5;
  return Math.max(0, Math.min(0.5, Math.floor(Math.min(...finite) * 10) / 10));
}

/** A 0–1 score axis: [scoreAxisFloor(scores), 1], ticks on a nice step, never past 1. */
export function scoreAxis(scores: readonly (number | null | undefined)[]): LinearAxis {
  const floor = scoreAxisFloor(scores);
  const step = niceStep(floor, 1, 5);
  const decimals = Math.max(1, stepDecimals(step));
  return { kind: "linear", min: floor, max: 1, interval: step, decimals, format: unitFormat("score", decimals) };
}

/** 1, 10, 100, 1k, 10k … (with the unit's suffix), for decade ticks. */
function decadeLabel(value: number, unit: AxisUnit): string {
  const body =
    value >= 1000
      ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value).toLowerCase()
      : value >= 1
        ? String(Math.round(value))
        : String(Number(value.toPrecision(2)));
  return unit === "ratio" ? `${body}×` : body;
}

/**
 * A log-10 axis over positive ratios, whole decades wide. 0 has no place on a log axis:
 * `place` draws it at the floor (a decade under the smallest positive value, at most 0.1),
 * and the caller says so in its tooltip and table.
 */
export function logAxis(
  values: readonly (number | null | undefined)[],
  options: { unit?: AxisUnit; include?: readonly (number | null | undefined)[] } = {},
): LogAxis {
  const positive = [...values, ...(options.include ?? [])].filter(
    (v): v is number => typeof v === "number" && Number.isFinite(v) && v > 0,
  );
  const hasZero = values.some((v) => v === 0);
  const lowest = positive.length ? Math.min(...positive) : 1;
  let min = 10 ** Math.floor(Math.log10(lowest));
  if (hasZero) min = Math.min(min / 10, 0.1);
  const max = 10 ** Math.ceil(Math.log10(Math.max(...positive, min * 10)));
  const unit = options.unit ?? "ratio";
  return {
    kind: "log",
    min,
    max: max <= min ? min * 10 : max,
    format: (v) => decadeLabel(v, unit),
    place: (v) => (v > min ? v : min),
  };
}

/** ECharts yAxis options for an axis (explicit bounds, so the labels are the ones computed here). */
export function echartsValueAxis(axis: ValueAxis, fontSize = 10) {
  if (axis.kind === "log")
    return {
      type: "log" as const,
      logBase: 10,
      min: axis.min,
      max: axis.max,
      axisLabel: { formatter: axis.format, fontSize },
    };
  return {
    type: "value" as const,
    min: axis.min,
    max: axis.max,
    interval: axis.interval,
    axisLabel: { formatter: axis.format, fontSize },
  };
}

/** Where a value sits on the axis, 0 (bottom) to 1 (top). */
export function axisFraction(axis: ValueAxis, value: number): number {
  if (axis.kind === "log") {
    const v = axis.place(value);
    return (Math.log10(v) - Math.log10(axis.min)) / (Math.log10(axis.max) - Math.log10(axis.min));
  }
  return (value - axis.min) / (axis.max - axis.min || 1);
}

export interface ThresholdLine {
  value: number;
  label: string;
  color: string;
}

/**
 * markLine data for threshold lines on `axis`, labels de-collided: lines whose labels would
 * overlap (closer than `minGap` of the axis height) are drawn separately but share one label,
 * joined with " · " on the upper line. Lines off the axis are dropped.
 */
export function thresholdMarkLines(
  lines: readonly ThresholdLine[],
  axis: ValueAxis,
  options: { minGap?: number; labelColor: string } = { labelColor: "#939aa7" },
) {
  const minGap = options.minGap ?? 0.12;
  const onAxis = lines
    .filter((l) => Number.isFinite(l.value) && l.value >= axis.min && l.value <= axis.max)
    .map((l) => ({ ...l, at: axisFraction(axis, l.value) }))
    .sort((a, b) => a.at - b.at);
  // Group lines bottom-up: a line joins the group whose top line is within minGap of it.
  const groups: (typeof onAxis)[] = [];
  for (const line of onAxis) {
    const last = groups.at(-1);
    if (last && line.at - last.at(-1)!.at < minGap) last.push(line);
    else groups.push([line]);
  }
  return groups.flatMap((group) =>
    group.map((line, i) => ({
      yAxis: line.value,
      lineStyle: { color: line.color, width: 1, type: "solid" as const },
      label:
        i === group.length - 1
          ? {
              show: true,
              formatter: group.map((l) => l.label).join(" · "),
              color: options.labelColor,
              position: "insideEndTop" as const,
            }
          : { show: false },
    })),
  );
}
