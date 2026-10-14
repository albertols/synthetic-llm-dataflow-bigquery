/**
 * Exact port of `sdfb_core.rag.serialize.serialize_row`: a row rendered as the
 * GReaT sentence "col is value, col is value, …" (Borisov et al. 2023,
 * arXiv 2210.06280) in a fixed column order. Pinned by `great_serialize.json`.
 *
 * Values render like Python's `str()`: None → "null", booleans → "true"/"false",
 * floats with `repr()` (1.0, 1e-05, 1e+16, -0.0), datetimes with a space
 * separator and "+00:00" offsets. JSON cannot tell a float 1.0 from an int 1,
 * so a float is either tagged (`{ py: "float", value }`) or its column is
 * listed in `floatColumns`. Values are not escaped: a comma inside a value is
 * indistinguishable from the separator, exactly as in the pipeline.
 */
export type PyTagged =
  | { py: "int"; value: number | bigint | string }
  | { py: "float"; value: number }
  | { py: "datetime"; value: string }
  | { py: "date"; value: string }
  | { py: "decimal"; value: string };

export type GreatValue = null | undefined | boolean | number | bigint | string | PyTagged;

/** Python's `repr(float)`: shortest round-trip digits, exponent when the decimal exponent is < -4 or ≥ 16. */
export function pyFloatRepr(value: number): string {
  if (Number.isNaN(value)) return "nan";
  if (value === Infinity) return "inf";
  if (value === -Infinity) return "-inf";
  if (value === 0) return Object.is(value, -0) ? "-0.0" : "0.0";
  const sign = value < 0 ? "-" : "";
  const [mantissa, exponentText] = Math.abs(value).toExponential().split("e") as [string, string];
  const exponent = Number(exponentText);
  const digits = mantissa.replace(".", "");
  if (exponent < -4 || exponent >= 16) {
    const head = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
    const abs = Math.abs(exponent);
    return `${sign}${head}e${exponent < 0 ? "-" : "+"}${abs < 10 ? `0${abs}` : abs}`;
  }
  if (exponent >= 0) {
    const whole = digits.slice(0, exponent + 1).padEnd(exponent + 1, "0");
    const fraction = digits.slice(exponent + 1) || "0";
    return `${sign}${whole}.${fraction}`;
  }
  return `${sign}0.${"0".repeat(-exponent - 1)}${digits}`;
}

const ISO = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:[.,](\d{1,6}))?(Z|[+-]\d{2}(?::?\d{2})?)?$/;

/** Python's `str(datetime.fromisoformat(iso))`. */
export function pyDatetimeStr(iso: string): string {
  const match = ISO.exec(iso.trim());
  if (!match) return iso;
  const [, date, hh, mm, ss = "00", fraction, zone] = match;
  const micro = fraction ? fraction.padEnd(6, "0") : "";
  let text = `${date} ${hh}:${mm}:${ss}${micro && Number(micro) !== 0 ? `.${micro}` : ""}`;
  if (zone) {
    if (zone === "Z") text += "+00:00";
    else {
      const compact = zone.replace(":", "");
      text += `${compact.slice(0, 3)}:${compact.length >= 5 ? compact.slice(3, 5) : "00"}`;
    }
  }
  return text;
}

/** Python's `str(value)` as `_render_value` applies it. */
export function pyStr(value: GreatValue, isFloat = false): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "number") {
    if (isFloat || !Number.isInteger(value) || Object.is(value, -0)) return pyFloatRepr(value);
    return String(value);
  }
  if (typeof value === "string") return value;
  switch (value.py) {
    case "int":
      return String(value.value);
    case "float":
      return pyFloatRepr(value.value);
    case "datetime":
      return pyDatetimeStr(value.value);
    case "date":
    case "decimal":
      return value.value;
  }
}

export interface GreatOptions {
  /** Columns whose plain numbers are Python floats (render 42 as "42.0"). */
  floatColumns?: Iterable<string>;
}

export function serializeGreat(
  row: Readonly<Record<string, GreatValue>>,
  columnOrder: readonly string[],
  options: GreatOptions = {},
): string {
  const floats = new Set(options.floatColumns ?? []);
  return columnOrder.map((column) => `${column} is ${pyStr(row[column], floats.has(column))}`).join(", ");
}
