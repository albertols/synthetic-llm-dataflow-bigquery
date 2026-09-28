import { render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Mermaid, withFlowchartDirection } from "./Mermaid";

// The real renderer is mermaid itself (a lazy chunk jsdom cannot lay out); this stand-in
// shows the source it was handed and fails on demand, the way MermaidRender reports errors.
vi.mock("./MermaidRender", () => ({
  default: function FakeRender({
    chart,
    ariaLabel,
    onError,
  }: {
    chart: string;
    ariaLabel: string;
    onError?: (message: string | null) => void;
  }) {
    const broken = chart.includes("BROKEN");
    useEffect(() => onError?.(broken ? "Parse error on line 2" : null), [broken, onError]);
    return broken ? (
      <p>Diagram not shown: {ariaLabel}. Diagram failed to render: Parse error on line 2</p>
    ) : (
      <pre data-testid="source">{chart}</pre>
    );
  },
}));

function stubViewport(narrow: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: narrow && query === "(max-width: 767px)",
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

const CHART = "flowchart LR\n  A --> B";

afterEach(() => vi.restoreAllMocks());

describe("Mermaid", () => {
  it("rewrites only a left-to-right flowchart header", () => {
    expect(withFlowchartDirection(CHART, "TB")).toBe("flowchart TB\n  A --> B");
    expect(withFlowchartDirection("graph RL\n  A --> B", "TB")).toBe("graph TB\n  A --> B");
    expect(withFlowchartDirection("flowchart TB\n  A --> B", "BT")).toBe("flowchart TB\n  A --> B");
    expect(withFlowchartDirection("sequenceDiagram\n  A->>B: hi", "TB")).toBe("sequenceDiagram\n  A->>B: hi");
  });

  it("lays a flowchart out top-to-bottom below md when asked", async () => {
    stubViewport(true);
    render(<Mermaid chart={CHART} narrowDirection="TB" ariaLabel="A flows into B" />);
    expect(await screen.findByTestId("source", {}, { timeout: 10_000 })).toHaveTextContent("flowchart TB");
    expect(screen.getByRole("img", { name: "A flows into B" })).toBeInTheDocument();
  });

  it("keeps the authored direction on wide screens, and without the option", async () => {
    stubViewport(false);
    const { unmount } = render(<Mermaid chart={CHART} narrowDirection="TB" ariaLabel="A flows into B" />);
    expect(await screen.findByTestId("source", {}, { timeout: 10_000 })).toHaveTextContent("flowchart LR");
    unmount();
    stubViewport(true);
    render(<Mermaid chart={CHART} ariaLabel="A flows into B" />);
    expect(await screen.findByTestId("source", {}, { timeout: 10_000 })).toHaveTextContent("flowchart LR");
  });

  it("stops being an image when mermaid fails, so screen readers read the error", async () => {
    stubViewport(false);
    render(<Mermaid chart={"flowchart LR\n  BROKEN -->"} ariaLabel="A flows into B" />);
    expect(await screen.findByText(/Diagram failed to render/, {}, { timeout: 10_000 })).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText(/Diagram not shown: A flows into B/)).toBeInTheDocument();
  });
});
