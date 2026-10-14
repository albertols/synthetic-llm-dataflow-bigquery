/**
 * The shared value-axis helper: adjacent tick labels never repeat (the QA
 * finding "0.90 0.90 0.90"), score axes stay on [floor, 1] (never 0.40–1.60),
 * lifts get whole log decades with 0 at the floor, and threshold labels that
 * would overlap share one label.
 */
import { describe, expect, it } from "vitest";

import { mulberry32 } from "@synthetic-platform/stats";

import {
  axisFraction,
  linearAxis,
  logAxis,
  niceStep,
  scoreAxis,
  stepDecimals,
  thresholdMarkLines,
  type LinearAxis,
} from "./chartAxis";

/** Every tick an explicit min / max / interval axis draws, and its label. */
function tickLabels(axis: LinearAxis): string[] {
  const out: string[] = [];
  const count = Math.round((axis.max - axis.min) / axis.interval);
  for (let i = 0; i <= count; i += 1) out.push(axis.format(axis.min + i * axis.interval));
  return out;
}

const distinctNeighbours = (labels: string[]) => labels.every((l, i) => i === 0 || l !== labels[i - 1]);

describe("linearAxis", () => {
  it.each([
    ["model fidelity score, 0.897–0.903", [0.897, 0.8995, 0.903], "plain"],
    ["model diversity score, 0.97–0.98", [0.971, 0.975, 0.979], "plain"],
    ["tiny KS values", [0.0012, 0.0013, 0.0015], "plain"],
    ["shares a tenth of a percent apart", [0.001, 0.0025, 0.004], "share"],
    ["ratios near 1", [0.99, 1.0, 1.01], "ratio"],
    ["a wide count", [0, 40_000, 100_000], "count"],
    ["a signed delta", [-0.2, 0.05, 0.3], "plain"],
  ] as const)("%s: adjacent ticks never share a label", (_, values, unit) => {
    const axis = linearAxis(values, { unit });
    const labels = tickLabels(axis);
    expect(distinctNeighbours(labels), labels.join(" ")).toBe(true);
    expect(axis.min).toBeLessThanOrEqual(Math.min(...values));
    expect(axis.max).toBeGreaterThanOrEqual(Math.max(...values));
    expect(labels.length).toBeLessThanOrEqual(12);
  });

  it("holds over random ranges of every magnitude", () => {
    const rand = mulberry32(20260929);
    for (let i = 0; i < 500; i += 1) {
      const magnitude = 10 ** Math.floor(rand() * 12 - 6);
      const lo = (rand() - 0.5) * 10 * magnitude;
      const hi = lo + rand() * 5 * magnitude;
      const axis = linearAxis([lo, hi]);
      const labels = tickLabels(axis);
      expect(distinctNeighbours(labels), `${lo}–${hi}: ${labels.join(" ")}`).toBe(true);
      expect(labels.every((l) => !/NaN|Infinity/.test(l))).toBe(true);
    }
  });

  it("pads a single value and ignores null and non-finite values", () => {
    const axis = linearAxis([5, null, Number.NaN, Number.POSITIVE_INFINITY]);
    expect(axis.min).toBeLessThan(5);
    expect(axis.max).toBeGreaterThan(5);
    expect(distinctNeighbours(tickLabels(axis))).toBe(true);
  });

  it("picks 1 / 2 / 2.5 / 5 steps and the digits they need", () => {
    expect(niceStep(0.897, 0.903, 5)).toBeCloseTo(0.002, 12);
    expect(tickLabels(linearAxis([0.897, 0.903]))).toEqual(["0.896", "0.898", "0.900", "0.902", "0.904"]);
    expect(niceStep(0, 1, 4)).toBe(0.25);
    expect(stepDecimals(0.25)).toBe(2);
    expect(stepDecimals(5)).toBe(0);
    expect(stepDecimals(0.0001)).toBe(4);
  });
});

describe("scoreAxis", () => {
  it("never goes past 1, and floors at 0.5 or lower (the radar rule)", () => {
    for (const scores of [[0.897, 0.903], [1, 1, 1], [0.99], [0.23, 0.9], [0.04]]) {
      const axis = scoreAxis(scores);
      expect(axis.max).toBe(1);
      expect(axis.min).toBeLessThanOrEqual(Math.min(...scores));
      expect(axis.min).toBeGreaterThanOrEqual(0);
      expect(distinctNeighbours(tickLabels(axis))).toBe(true);
    }
    // The QA finding: an all-1.0 integrity score drew on 0.40–1.60.
    expect(scoreAxis([1, 1, 1])).toMatchObject({ min: 0.5, max: 1 });
    expect(scoreAxis([0.23, 0.9]).min).toBe(0.2);
  });
});

describe("logAxis", () => {
  it("spans whole decades, draws 0 at the floor and labels decades", () => {
    const axis = logAxis([0, 1.5, 3, 100_000], { unit: "ratio", include: [1] });
    expect(axis.min).toBe(0.1);
    expect(axis.max).toBe(100_000);
    expect(axis.place(0)).toBe(0.1);
    expect(axis.place(3)).toBe(3);
    expect([0.1, 1, 10, 100, 1000, 100_000].map(axis.format)).toEqual(["0.1×", "1×", "10×", "100×", "1k×", "100k×"]);
  });

  it("works with no positive value at all", () => {
    const axis = logAxis([0, 0]);
    expect(axis.min).toBeGreaterThan(0);
    expect(axis.max).toBeGreaterThan(axis.min);
  });
});

describe("thresholdMarkLines", () => {
  const lines = [
    { value: 2, label: "warn 2×", color: "#fab219" },
    { value: 5, label: "fail 5×", color: "#d03b3b" },
  ];

  it("merges labels that would overlap into one ('warn 2× · fail 5×')", () => {
    // Value lift on a 0.1×–100k× log axis: 2 and 5 sit 0.07 of the height apart.
    const axis = logAxis([0, 100_000], { unit: "ratio" });
    expect(axisFraction(axis, 5) - axisFraction(axis, 2)).toBeLessThan(0.12);
    const marks = thresholdMarkLines(lines, axis, { labelColor: "#939aa7" });
    expect(marks).toHaveLength(2);
    expect(marks.filter((m) => m.label.show).map((m) => m.label)).toEqual([
      expect.objectContaining({ formatter: "warn 2× · fail 5×" }),
    ]);
  });

  it("keeps separate labels when the lines are far apart, and drops lines off the axis", () => {
    const axis = linearAxis([0, 10]);
    const marks = thresholdMarkLines([...lines, { value: 50, label: "off", color: "#000" }], axis, {
      labelColor: "#939aa7",
    });
    expect(marks.map((m) => (m.label.show ? m.label.formatter : null))).toEqual(["warn 2×", "fail 5×"]);
  });
});
