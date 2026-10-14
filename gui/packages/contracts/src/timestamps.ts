/**
 * BigQuery TIMESTAMPs on the wire: `YYYY-MM-DDTHH:MM:SS.ffffffZ`, always UTC,
 * always six fraction digits (BigQuery's microsecond precision).
 *
 * Why fixed width: the BFF passes a registry row's `evaluated_at` back as a
 * query parameter (`evaluated_at = TIMESTAMP(@evaluated_at)`), so the string
 * must keep every microsecond a `Date` would drop; and the providers compare
 * timestamps as strings, which orders them correctly only when every one has
 * the same shape. Dependency-free (the web shell may import it).
 */

const TEXT = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?\s*(Z|UTC|[+-]\d{2}(?::?\d{2})?)?$/i;

/** Formats epoch milliseconds (whole) plus extra microseconds as the canonical form. */
function fromEpoch(epochMs: number, micros: number): string | null {
  const date = new Date(epochMs);
  if (Number.isNaN(date.getTime())) return null;
  return `${date.toISOString().slice(0, 19)}.${String(micros).padStart(6, "0")}Z`;
}

/**
 * Any BigQuery / ISO timestamp text → the canonical form; null when it is not a timestamp.
 * Accepts `2026-09-01`, `2026-09-01 10:00:00 UTC`, `2026-09-01T10:00:00.123Z`,
 * `…:00.123456789Z` (PreciseDate), `…+02:00`. Digits past the sixth are truncated.
 */
export function canonicalTimestamp(text: string): string | null {
  const m = TEXT.exec(text.trim());
  if (!m) return null;
  const [, day, hh = "00", mm = "00", ss = "00", fraction = "", zone = "Z"] = m;
  const micros = Number(fraction.slice(0, 6).padEnd(6, "0"));
  const wholeMs = Date.parse(`${day}T${hh}:${mm}:${ss}Z`);
  if (Number.isNaN(wholeMs)) return null;
  let offsetMs = 0;
  if (!/^(Z|UTC)$/i.test(zone)) {
    const [, sign, oh, om = "00"] = /^([+-])(\d{2}):?(\d{2})?$/.exec(zone) ?? [];
    offsetMs = (sign === "-" ? -1 : 1) * (Number(oh) * 60 + Number(om)) * 60_000;
  }
  return fromEpoch(wholeMs - offsetMs, micros);
}

/**
 * A filter bound (`from` / `to`) → the canonical form, rounded UP to the
 * microsecond. BigQuery rejects more than six fraction digits in
 * `TIMESTAMP(@from)`, and it stores microseconds, so for any stored value
 * `at >= from` and `at < to` hold exactly when they hold against the bound
 * rounded up. Null when the text is not a timestamp or a date.
 */
export function canonicalBound(text: string): string | null {
  const canonical = canonicalTimestamp(text);
  if (canonical === null) return null;
  const extra = (TEXT.exec(text.trim())?.[5] ?? "").slice(6);
  if (!/[1-9]/.test(extra)) return canonical;
  const wholeMs = Date.parse(`${canonical.slice(0, 19)}Z`);
  const micros = Number(canonical.slice(20, 26)) + 1;
  return micros === 1_000_000 ? fromEpoch(wholeMs + 1000, 0) : fromEpoch(wholeMs, micros);
}

/** Epoch seconds (float, as BigQuery's REST API may return) → the canonical form, rounded to the microsecond. */
export function timestampFromEpochSeconds(seconds: number): string | null {
  if (!Number.isFinite(seconds)) return null;
  let whole = Math.floor(seconds);
  let micros = Math.round((seconds - whole) * 1e6);
  if (micros === 1_000_000) {
    whole += 1;
    micros = 0;
  }
  return fromEpoch(whole * 1000, micros);
}

/** True when the text is already canonical (what the BFF emits and the mock writes). */
export function isCanonicalTimestamp(text: string): boolean {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/.test(text) && canonicalTimestamp(text) === text;
}
