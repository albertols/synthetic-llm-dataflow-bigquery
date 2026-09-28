/**
 * The list's "fail · warn · n/e" cell reads out a full breakdown whose parts
 * add up to metrics_total: fail + warn + pass + info + not evaluated, the
 * registry's identity since metrics_info (Ruling R37). Rows written before
 * that column (metrics_info NULL) name the remainder "info or other"; counts
 * that do not add up are called out in words, never hidden.
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
  info: number | null;
};

function renderCell(counts: Counts) {
  const e = evaluation({
    metrics_total: counts.total,
    metrics_pass: counts.pass,
    metrics_warn: counts.warn,
    metrics_fail: counts.fail,
    metrics_not_evaluated: counts.ne,
    metrics_info: counts.info,
  });
  return render(<MetricsCell e={e} />);
}

function srText(counts: Counts): string {
  const { container, unmount } = renderCell(counts);
  const text = container.querySelector(".sr-only")?.textContent ?? "";
  unmount();
  return text;
}

/** "10 fail · 50 warn · 300 pass · 17 info · 21 not evaluated = 398 metrics" → parts and total. */
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
  it("reads the real info bucket: fail + warn + pass + info + not evaluated = metrics_total", () => {
    const text = srText({ total: 398, pass: 300, warn: 50, fail: 10, ne: 21, info: 17 });
    expect(text).toBe("10 fail · 50 warn · 300 pass · 17 info · 21 not evaluated = 398 metrics");
    const { parts, total } = parse(text);
    expect(Object.values(parts).reduce((a, b) => a + b, 0)).toBe(total);
  });

  it("adds up across every mix of statuses, with or without metrics_info", () => {
    for (const fail of [0, 3])
      for (const warn of [0, 7])
        for (const pass of [0, 40])
          for (const ne of [0, 5, null])
            for (const info of [0, 11, null])
              for (const rest of info === null ? [0, 11] : [0]) {
                const total = fail + warn + pass + (ne ?? 0) + (info ?? 0) + rest;
                const text = srText({ total, pass, warn, fail, ne, info });
                const { parts, total: stated } = parse(text);
                expect(stated, text).toBe(total);
                expect(
                  Object.values(parts).reduce((a, b) => a + b, 0),
                  text,
                ).toBe(total);
                expect("info or other" in parts, text).toBe(info === null && rest > 0);
                expect("info" in parts, text).toBe(info !== null && info > 0);
              }
  });

  it("keeps the info-or-other remainder only for rows without metrics_info (old registry rows)", () => {
    const { parts, total } = parse(srText({ total: 12, pass: null, warn: null, fail: null, ne: null, info: null }));
    expect(parts).toEqual({ fail: 0, warn: 0, pass: 0, "info or other": 12 });
    expect(total).toBe(12);
    expect(srText({ total: null, pass: 1, warn: 0, fail: 0, ne: 0, info: 0 })).toBe("");
  });

  it("says in words when the counts exceed the total, instead of a negative remainder", () => {
    const counts = { total: 10, pass: 8, warn: 3, fail: 1, ne: 0, info: null };
    const text = srText(counts);
    expect(text).toBe("1 fail · 3 warn · 8 pass = 10 metrics; counts exceed total (12 counted)");
    expect(text).not.toMatch(/-\d/);
    const { container, unmount } = renderCell(counts);
    // Visible too, not only in the screen-reader text.
    expect(container.querySelector('[aria-hidden="true"]')?.textContent).toContain("counts exceed total (12 counted)");
    unmount();
    expect(srText({ ...counts, info: 4 })).toBe(
      "1 fail · 3 warn · 8 pass · 4 info = 10 metrics; counts exceed total (16 counted)",
    );
  });

  it("says when a row with metrics_info falls short of its total", () => {
    expect(srText({ total: 20, pass: 8, warn: 3, fail: 1, ne: 0, info: 2 })).toBe(
      "1 fail · 3 warn · 8 pass · 2 info = 20 metrics; counts fall short of total (14 counted)",
    );
  });
});
