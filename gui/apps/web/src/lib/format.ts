/**
 * Number and date formatting for every view. Fixed "en-US" so screenshots,
 * tests and the table fallback read the same everywhere; missing or
 * non-finite values render as an em dash, never "NaN".
 */

export const MISSING = "—";

const LOCALE = "en-US";

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** 1284.5 → "1,284.5"; `digits` caps the fraction digits (default 3). */
export function formatNumber(value: number | null | undefined, digits = 3): string {
  if (!isFiniteNumber(value)) return MISSING;
  return new Intl.NumberFormat(LOCALE, { maximumFractionDigits: digits }).format(value);
}

/** Fixed fraction digits: 0.04 → "0.040" (digits 3). For metric values in columns. */
export function formatFixed(value: number | null | undefined, digits = 3): string {
  if (!isFiniteNumber(value)) return MISSING;
  return new Intl.NumberFormat(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

/** 1284 → "1.3K", 12_900_000 → "12.9M". For stat tiles and axis ticks. */
export function formatCompact(value: number | null | undefined, digits = 1): string {
  if (!isFiniteNumber(value)) return MISSING;
  return new Intl.NumberFormat(LOCALE, { notation: "compact", maximumFractionDigits: digits }).format(value);
}

/** A share in [0, 1] → "4.2%". */
export function formatPercent(share: number | null | undefined, digits = 1): string {
  if (!isFiniteNumber(share)) return MISSING;
  return new Intl.NumberFormat(LOCALE, { style: "percent", maximumFractionDigits: digits }).format(share);
}

/** Integers with thousands separators: 10000000 → "10,000,000". */
export function formatCount(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return MISSING;
  return new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 }).format(value);
}

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

/** Decimal (SI) bytes, as BigQuery bills them: 10e9 → "10 GB". */
export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (!isFiniteNumber(bytes) || bytes < 0) return MISSING;
  let unit = 0;
  let scaled = bytes;
  while (scaled >= 1000 && unit < BYTE_UNITS.length - 1) {
    scaled /= 1000;
    unit += 1;
  }
  return `${formatNumber(scaled, unit === 0 ? 0 : digits)} ${BYTE_UNITS[unit]}`;
}

/** Seconds → "48 s", "3 min 20 s", "1 h 05 min". */
export function formatDuration(seconds: number | null | undefined): string {
  if (!isFiniteNumber(seconds) || seconds < 0) return MISSING;
  if (seconds < 60) return `${formatNumber(seconds, seconds < 10 ? 1 : 0)} s`;
  const totalMinutes = Math.floor(seconds / 60);
  if (totalMinutes < 60) {
    const rest = Math.round(seconds - totalMinutes * 60);
    return rest ? `${totalMinutes} min ${rest} s` : `${totalMinutes} min`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes - hours * 60;
  return `${hours} h ${String(minutes).padStart(2, "0")} min`;
}

function toDate(value: string | number | Date | null | undefined): Date | undefined {
  if (value === null || value === undefined || value === "") return undefined;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date;
}

/** ISO timestamp → "2026-09-27 14:05 UTC" (UTC, unambiguous across machines). */
export function formatDateTime(value: string | number | Date | null | undefined): string {
  const date = toDate(value);
  if (!date) return MISSING;
  const iso = date.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

/** ISO timestamp → "2026-09-27". */
export function formatDate(value: string | number | Date | null | undefined): string {
  const date = toDate(value);
  return date ? date.toISOString().slice(0, 10) : MISSING;
}

/** The catalogue's value kinds (evaluation_metrics.value_kind). */
export type ValueKind = "distance" | "share" | "ratio" | "bits" | "correlation_delta" | "auc" | "score" | "count";

/** A metric value in the unit its kind implies: shares as %, bits with a unit, counts as integers. */
export function formatMetricValue(value: number | null | undefined, kind?: ValueKind | (string & {}) | null): string {
  if (!isFiniteNumber(value)) return MISSING;
  switch (kind) {
    case "share":
      return formatPercent(value, value !== 0 && Math.abs(value) < 0.01 ? 2 : 1);
    case "count":
      return formatCount(value);
    case "bits":
      return `${formatFixed(value, 2)} bits`;
    case "ratio":
      return `${formatFixed(value, 2)}×`;
    case "score":
    case "auc":
      return formatFixed(value, 2);
    default:
      return Math.abs(value) >= 1000 ? formatNumber(value, 1) : formatFixed(value, 3);
  }
}

/** Any cell value for the table fallback: numbers formatted, objects as compact JSON, empty as "—". */
export function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return MISSING;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return MISSING;
    return Number.isInteger(value) ? formatCount(value) : formatNumber(value, 4);
  }
  if (typeof value === "bigint") return formatCount(Number(value));
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value instanceof Date) return formatDateTime(value);
  if (typeof value === "string") return value;
  try {
    const json = JSON.stringify(value);
    return json.length > 120 ? `${json.slice(0, 117)}…` : json;
  } catch {
    return "[unserialisable value]";
  }
}
