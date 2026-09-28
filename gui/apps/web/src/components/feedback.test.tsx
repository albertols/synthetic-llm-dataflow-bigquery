import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Banner, Callout } from "./Callout";
import { StatTile } from "./StatTile";
import { StatusPill } from "./StatusPill";

describe("Callout and Banner", () => {
  it("labels the tone in words, not colour alone", () => {
    render(<Callout tone="docs-differ">DESIGN.md says deciles; the code interpolates.</Callout>);
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("Docs differ");
    expect(note).toHaveAttribute("data-tone", "docs-differ");
  });

  it("banners are live and dismissible", async () => {
    const onDismiss = vi.fn();
    render(
      <Banner tone="warn" title="3-D view unavailable" onDismiss={onDismiss}>
        Showing the 2-D projection.
      </Banner>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("3-D view unavailable");
    await userEvent.setup().click(screen.getByRole("button", { name: "Dismiss: 3-D view unavailable" }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});

describe("StatTile", () => {
  it("shows the value, unit and a signed delta in words", () => {
    render(
      <StatTile
        label="Overall score"
        value={0.87}
        format={(v) => v.toFixed(2)}
        delta={{ value: -0.04, vs: "the previous run", goodWhen: "up", format: (v) => v.toFixed(2) }}
        trend={[0.9, 0.88, 0.87]}
      />,
    );
    const tile = screen.getByRole("group", { name: "Overall score" });
    expect(tile).toHaveTextContent("0.87");
    expect(tile).toHaveTextContent("−0.04 vs the previous run");
    expect(tile).toHaveTextContent("Trend over 3 points, from 0.9 to 0.87.");
  });

  it("never prints NaN", () => {
    render(<StatTile label="Rows" value={Number.NaN} />);
    expect(screen.getByRole("group", { name: "Rows" })).toHaveTextContent("—");
  });

  it("compacts large counts by default", () => {
    render(<StatTile label="Rows generated" value={90_000_000} />);
    expect(screen.getByRole("group", { name: "Rows generated" })).toHaveTextContent("90M");
  });
});

describe("StatusPill", () => {
  it("knows the scope statuses", () => {
    render(<StatusPill status="contaminated" />);
    expect(screen.getByText("Contaminated").closest("[data-tone]")).toHaveAttribute("data-tone", "critical");
  });

  it("takes a tone override for statuses it does not know", () => {
    render(<StatusPill status="stale_reference" tone="warn" label="Stale reference" />);
    expect(screen.getByText("Stale reference").closest("[data-tone]")).toHaveAttribute("data-tone", "warn");
  });
});
