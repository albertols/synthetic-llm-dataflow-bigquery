import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { STALE_TITLE } from "@/lib/runState";

import { Banner, Callout } from "./Callout";
import { RunStatusPill } from "./RunStatusPill";
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
    const pill = screen.getByText("Stale reference").closest("[data-tone]");
    expect(pill).toHaveAttribute("data-tone", "warn");
    // An unknown status borrows the tone's icon.
    expect(pill?.querySelector("svg.lucide-triangle-alert")).not.toBeNull();
  });

  it("keeps a known status's icon under a tone override, whatever its case", () => {
    render(<StatusPill status="Unknown" tone="warn" />);
    const pill = screen.getByText("Unknown").closest("[data-tone]");
    expect(pill).toHaveAttribute("data-tone", "warn");
    expect(pill?.querySelector("svg.lucide-circle-help")).not.toBeNull();
    expect(pill?.querySelector("svg.lucide-triangle-alert")).toBeNull();
  });

  it("shows a stale run as needing attention, with no spinner and with what the registry holds", () => {
    render(<StatusPill status="STALE" title="Registry status RUNNING" />);
    const pill = screen.getByText("Stale").closest("[data-tone]")!;
    expect(pill).toHaveAttribute("data-tone", "warn");
    expect(pill).toHaveAttribute("title", "Registry status RUNNING");
    // Amber like a warning, but not the triangle "Succeeded with warnings" wears: only the tone is shared.
    expect(pill.querySelector("svg.lucide-circle-slash")).not.toBeNull();
    expect(pill.querySelector("svg.lucide-triangle-alert")).toBeNull();
    expect(pill.querySelector("svg")).not.toHaveClass("motion-safe:animate-spin");
    // The title is not hover-only: a screen reader reads it after the label.
    expect(pill).toHaveTextContent("Stale (Registry status RUNNING)");
  });

  it("gives no other run status the stale pill's look", () => {
    const look = (status: string) => {
      const { container, unmount } = render(<StatusPill status={status} />);
      const pill = container.querySelector("[data-tone]")!;
      const icon = [...pill.querySelector("svg")!.classList].find((c) => c.startsWith("lucide-") && c !== "lucide");
      unmount();
      return `${pill.getAttribute("data-tone")} ${icon}`;
    };
    const others = ["RUNNING", "SUCCEEDED", "SUCCEEDED_WITH_WARNINGS", "PARTIAL", "SKIPPED", "FAILED"].map(look);
    expect(others).not.toContain(look("STALE"));
    expect(look("STALE")).toBe("warn lucide-circle-slash");
  });

  it("keeps the spinner for a run that is running, and no title unless one is given", () => {
    render(<StatusPill status="RUNNING" />);
    const pill = screen.getByText("Running").closest("[data-tone]")!;
    expect(pill).toHaveAttribute("data-tone", "info");
    expect(pill.querySelector("svg")).toHaveClass("motion-safe:animate-spin");
    expect(pill).not.toHaveAttribute("title");
    expect(pill).toHaveTextContent(/^Running$/);
  });
});

describe("RunStatusPill", () => {
  const RECORDED = "2026-09-20T10:00:00.000000Z";
  const after = (hours: number, ms = 0) => Date.parse(RECORDED) + hours * 3_600_000 + ms;
  const running = { event: "RUNNING", status: "RUNNING", recorded_at: RECORDED };

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows Running up to 24 hours after the RUNNING event", () => {
    render(<RunStatusPill row={running} now={after(24)} />);
    const pill = screen.getByText("Running").closest("[data-status]")!;
    expect(pill).toHaveAttribute("data-status", "RUNNING");
    expect(pill).toHaveAttribute("data-tone", "info");
    expect(pill).not.toHaveAttribute("title");
  });

  it("shows Stale once the RUNNING event is more than 24 hours old, and says what the registry holds", () => {
    render(<RunStatusPill row={running} now={after(24, 1)} />);
    const pill = screen.getByText("Stale").closest("[data-status]")!;
    expect(pill).toHaveAttribute("data-status", "STALE");
    expect(pill).toHaveAttribute("data-tone", "warn");
    expect(pill).toHaveAttribute("title", STALE_TITLE);
    expect(STALE_TITLE).toMatch(/^Registry status RUNNING/);
    expect(screen.queryByText("Running")).toBeNull();
  });

  it("shows a finished evaluation's own status whatever the clock says", () => {
    render(
      <RunStatusPill
        row={{ event: "FINAL", status: "FAILED", recorded_at: RECORDED }}
        now={after(24 * 365)}
        size="sm"
      />,
    );
    expect(screen.getByText("Failed").closest("[data-status]")).toHaveAttribute("data-status", "FAILED");
  });

  it("reads the clock when none is injected", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(after(23));
    const first = render(<RunStatusPill row={running} />);
    expect(screen.getByText("Running")).toBeInTheDocument();
    first.unmount();
    vi.setSystemTime(after(25));
    render(<RunStatusPill row={running} />);
    expect(screen.getByText("Stale")).toBeInTheDocument();
  });
});
