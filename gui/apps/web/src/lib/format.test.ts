import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatCell,
  formatCompact,
  formatCount,
  formatDateTime,
  formatDuration,
  formatFixed,
  formatMetricValue,
  formatNumber,
  formatPercent,
  MISSING,
} from "./format";

describe("format", () => {
  it("never prints NaN or undefined", () => {
    for (const bad of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(formatNumber(bad)).toBe(MISSING);
      expect(formatPercent(bad)).toBe(MISSING);
      expect(formatMetricValue(bad, "share")).toBe(MISSING);
      expect(formatCell(bad)).toBe(MISSING);
    }
    expect(formatDateTime("not a date")).toBe(MISSING);
  });

  it("formats numbers the en-US way", () => {
    expect(formatNumber(1284.5)).toBe("1,284.5");
    expect(formatFixed(0.04)).toBe("0.040");
    expect(formatCompact(12_900_000)).toBe("12.9M");
    expect(formatCount(10_000_000)).toBe("10,000,000");
    expect(formatPercent(0.0421)).toBe("4.2%");
  });

  it("formats bytes in SI units, as BigQuery bills them", () => {
    expect(formatBytes(10e9)).toBe("10 GB");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1_536_000)).toBe("1.5 MB");
  });

  it("formats durations", () => {
    expect(formatDuration(4.24)).toBe("4.2 s");
    expect(formatDuration(48)).toBe("48 s");
    expect(formatDuration(200)).toBe("3 min 20 s");
    expect(formatDuration(3900)).toBe("1 h 05 min");
  });

  it("rounds durations once, at the unit shown", () => {
    expect(formatDuration(9.97)).toBe("10 s");
    expect(formatDuration(59.6)).toBe("1 min");
    expect(formatDuration(119.7)).toBe("2 min");
    expect(formatDuration(119.4)).toBe("1 min 59 s");
    expect(formatDuration(3599.6)).toBe("1 h 00 min");
    expect(formatDuration(7199.9)).toBe("2 h 00 min");
  });

  it("formats metric values by kind", () => {
    expect(formatMetricValue(0.0136, "distance")).toBe("0.014");
    expect(formatMetricValue(0.042, "share")).toBe("4.2%");
    expect(formatMetricValue(0.004, "share")).toBe("0.4%");
    expect(formatMetricValue(1.25, "ratio")).toBe("1.25×");
    expect(formatMetricValue(3.1, "bits")).toBe("3.10 bits");
    expect(formatMetricValue(10_000, "count")).toBe("10,000");
  });

  it("formats table cells", () => {
    expect(formatCell(0.31)).toBe("0.31");
    expect(formatCell(1200)).toBe("1,200");
    expect(formatCell(true)).toBe("true");
    expect(formatCell({ a: 1 })).toBe('{"a":1}');
    expect(formatDateTime("2026-09-27T14:05:09Z")).toBe("2026-09-27 14:05 UTC");
  });
});
