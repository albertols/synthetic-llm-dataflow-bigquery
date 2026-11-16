/**
 * The per-table scorecards at scale (ADR 0036): a card groups its integrity units by table once, never per
 * visible row per scroll; the windowed rows are sized in rem, so a larger default font size (WCAG 1.4.4)
 * never makes them overlap.
 */
import type { MetricRow } from "@contracts/api";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { familyCards } from "../lib/model";
import { forestDetail } from "../test/fixtures";
import { Scorecards } from "./Scorecards";

// 120 tables of five families render at once: give a busy worker room.
vi.setConfig({ testTimeout: 60_000 });

/** `rows` behind a proxy that counts every row read (array methods and for…of read through it). */
function counted(rows: readonly MetricRow[]) {
  let reads = 0;
  const proxy = new Proxy([...rows], {
    get(target, key, receiver) {
      if (typeof key === "string" && /^\d+$/.test(key)) reads += 1;
      return Reflect.get(target, key, receiver) as unknown;
    },
  });
  return {
    rows: proxy,
    reads: () => reads,
    reset: () => {
      reads = 0;
    },
  };
}

const FRAME = "Per-table scores, Integrity: 120 tables";

describe("per-table integrity scores at scale (ADR 0036)", () => {
  it("group the units by table once: scrolling the window reads no metric row", () => {
    const cards = familyCards(forestDetail());
    const integrity = cards.find((c) => c.family === "integrity")!;
    const probe = counted(integrity.metrics);
    render(
      <Scorecards cards={cards.map((c) => (c === integrity ? { ...c, metrics: probe.rows } : c))} onOpen={() => {}} />,
    );
    const frame = screen.getByRole("region", { name: FRAME });
    probe.reset();
    for (const top of [24 * 30, 24 * 60, 24 * 100]) fireEvent.scroll(frame, { target: { scrollTop: top } });
    expect(within(frame).getByText("T_110")).toBeInTheDocument();
    expect(probe.reads()).toBe(0);
  });
});

describe("windowed rows are sized in rem (WCAG 1.4.4)", () => {
  afterEach(() => {
    document.documentElement.style.fontSize = "";
  });

  it("at the default 16 px root a row is 24 px and the frame twelve rows", () => {
    render(<Scorecards cards={familyCards(forestDetail())} onOpen={() => {}} />);
    const frame = screen.getByRole("region", { name: FRAME });
    expect(frame).toHaveStyle({ height: "288px" });
    expect(frame.querySelector('li[aria-posinset="2"]')).toHaveStyle({ top: "24px", height: "24px" });
  });

  it("at a 20 px root a row is 30 px (1.5 rem, the row's h-6), so rows never overlap", () => {
    document.documentElement.style.fontSize = "20px";
    render(<Scorecards cards={familyCards(forestDetail())} onOpen={() => {}} />);
    const frame = screen.getByRole("region", { name: FRAME });
    expect(frame).toHaveStyle({ height: "360px" });
    expect(within(frame).getByRole("list")).toHaveStyle({ height: "3600px" });
    expect(frame.querySelector('li[aria-posinset="2"]')).toHaveStyle({ top: "30px", height: "30px" });
  });
});
