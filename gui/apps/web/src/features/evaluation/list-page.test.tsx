/**
 * The list's "fail · warn · n/e" cell reads out a full breakdown. The registry
 * row has no info/other count, so "info or other" is a remainder the cell
 * derives — a second breakdown definition, pinned here: its parts always add
 * up to metrics_total.
 */
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MetricsCell } from "./ListPage";
import { evaluation } from "./test/fixtures";

type Counts = {
  total: number | null;
  pass: number | null;
  warn: number | null;
  fail: number | null;
  ne: number | null;
};

function srText(counts: Counts): string {
  const e = evaluation({
    metrics_total: counts.total,
    metrics_pass: counts.pass,
    metrics_warn: counts.warn,
    metrics_fail: counts.fail,
    metrics_not_evaluated: counts.ne,
  });
  const { container, unmount } = render(<MetricsCell e={e} />);
  const text = container.querySelector(".sr-only")?.textContent ?? "";
  unmount();
  return text;
}

/** "10 fail · 50 warn · 300 pass · 17 info or other · 21 not evaluated = 398 metrics" → parts and total. */
function parse(text: string): { parts: Record<string, number>; total: number } {
  const [lhs, rhs] = text.split(" = ");
  const parts = Object.fromEntries(
    lhs!.split(" · ").map((part) => {
      const [, n, label] = /^(\d+) (.+)$/.exec(part)!;
      return [label!, Number(n)];
    }),
  );
  return { parts, total: Number(/^(\d+) metrics$/.exec(rhs!)![1]) };
}

describe("the list's metrics breakdown (screen-reader text)", () => {
  it("adds up to metrics_total, with the info-or-other remainder when there is one", () => {
    const text = srText({ total: 398, pass: 300, warn: 50, fail: 10, ne: 21 });
    expect(text).toBe("10 fail · 50 warn · 300 pass · 17 info or other · 21 not evaluated = 398 metrics");
    const { parts, total } = parse(text);
    expect(Object.values(parts).reduce((a, b) => a + b, 0)).toBe(total);
  });

  it("adds up across every mix of statuses, and names no remainder when there is none", () => {
    for (const fail of [0, 3])
      for (const warn of [0, 7])
        for (const pass of [0, 40])
          for (const ne of [0, 5, null])
            for (const rest of [0, 11]) {
              const total = fail + warn + pass + (ne ?? 0) + rest;
              const text = srText({ total, pass, warn, fail, ne });
              const { parts, total: stated } = parse(text);
              expect(stated, text).toBe(total);
              expect(
                Object.values(parts).reduce((a, b) => a + b, 0),
                text,
              ).toBe(total);
              expect("info or other" in parts, text).toBe(rest > 0);
            }
  });

  it("treats missing status counts as zero and a missing total as unknown", () => {
    const { parts, total } = parse(srText({ total: 12, pass: null, warn: null, fail: null, ne: null }));
    expect(parts).toEqual({ fail: 0, warn: 0, pass: 0, "info or other": 12 });
    expect(total).toBe(12);
    expect(srText({ total: null, pass: 1, warn: 0, fail: 0, ne: 0 })).toBe("");
  });
});
