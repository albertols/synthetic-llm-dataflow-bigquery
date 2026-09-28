/**
 * BigQuery client values → the wire format the generated zod describes.
 *
 * `@google-cloud/bigquery` returns TIMESTAMP as `BigQueryTimestamp { value }`
 * (ISO text with 3 or 9 fraction digits), normalized to six (microseconds),
 * DATE/DATETIME as objects with `value`, INT64 as number (or string / BigInt /
 * `BigQueryInt` when wrapped), JSON as a parsed value or a string, NUMERIC as
 * `Big`. The field tree (from the generated `bqTables` metadata or a query's
 * own projection) says which conversion applies, recursively through RECORD
 * and REPEATED fields. Non-finite floats become null (JSON cannot carry them).
 */
import { canonicalTimestamp, timestampFromEpochSeconds, type BqField } from "@synthetic-platform/contracts";

function unwrap(value: unknown): unknown {
  if (value && typeof value === "object" && "value" in value) return value.value;
  return value;
}

/**
 * TIMESTAMP → `YYYY-MM-DDTHH:MM:SS.ffffffZ` (contracts/timestamps.ts). Never through
 * `Date` alone: BigQuery stores microseconds, and a registry `evaluated_at` sent back
 * as `TIMESTAMP(@evaluated_at)` must match to the microsecond.
 */
function toIsoTimestamp(value: unknown): unknown {
  const v = unwrap(value);
  // PreciseDate (a Date subclass) prints nanoseconds; a plain Date prints milliseconds.
  if (v instanceof Date) return canonicalTimestamp(v.toISOString()) ?? v;
  if (typeof v === "number") return timestampFromEpochSeconds(v) ?? v;
  if (typeof v !== "string") return v;
  if (/^-?\d+(\.\d+)?$/.test(v)) return timestampFromEpochSeconds(Number(v)) ?? v;
  return canonicalTimestamp(v) ?? v;
}

function toNumber(value: unknown, integer: boolean): unknown {
  const v = unwrap(value);
  if (v === null || v === undefined) return null;
  if (typeof v === "bigint") return Number(v);
  // Big (NUMERIC) and BigQueryInt expose valueOf/toString; anything else is not a number.
  const n = typeof v === "number" ? v : typeof v === "string" ? Number(v) : Number(v);
  if (!Number.isFinite(n)) return integer ? v : null;
  return n;
}

function toJson(value: unknown): unknown {
  if (typeof value !== "string") return value ?? null;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return value;
  }
}

function scalar(field: BqField, value: unknown): unknown {
  if (value === undefined) return null;
  if (value === null) return null;
  switch (field.type) {
    case "TIMESTAMP":
      return toIsoTimestamp(value);
    case "DATE":
    case "DATETIME":
    case "TIME":
      return String(unwrap(value));
    case "INT64":
    case "INTEGER":
      return toNumber(value, true);
    case "FLOAT64":
    case "FLOAT":
    case "NUMERIC":
    case "BIGNUMERIC":
      return toNumber(value, false);
    case "BOOL":
    case "BOOLEAN":
      return typeof value === "string" ? value === "true" : Boolean(value);
    case "JSON":
      return toJson(value);
    case "RECORD":
    case "STRUCT":
      return normalizeRow(value as Record<string, unknown>, field.fields ?? []);
    default:
      return value;
  }
}

export function normalizeRow(row: Record<string, unknown>, fields: readonly BqField[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const field of fields) {
    const value = row[field.name];
    if (field.mode === "REPEATED") out[field.name] = Array.isArray(value) ? value.map((v) => scalar(field, v)) : [];
    else out[field.name] = scalar(field, value);
  }
  return out;
}

export function normalizeRows(
  rows: readonly Record<string, unknown>[],
  fields: readonly BqField[],
): Record<string, unknown>[] {
  return rows.map((row) => normalizeRow(row, fields));
}
