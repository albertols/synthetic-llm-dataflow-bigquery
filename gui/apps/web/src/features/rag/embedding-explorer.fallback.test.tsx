/**
 * Review focus 4: WebGL unavailable or the context lost (an Intel iGPU reset,
 * a background tab). The explorer must fall back to its 2-D canvas view under
 * a banner that says why, keep every non-visual path (inspector, query,
 * legend, table twin) working, and offer the 2-D view by choice without a
 * warning.
 */
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DeckCanvasProps } from "@/components/DeckCanvas";
import { resetWebGLProbe } from "@/lib/webgl";

import { ExplorerSection } from "./explorer/ExplorerSection";
import { fixtureModel } from "./testFixtures";

let canvasProps: DeckCanvasProps | undefined;
vi.mock("@/components/DeckCanvas", () => ({
  default: (props: DeckCanvasProps) => {
    canvasProps = props;
    return (
      <button type="button" data-testid="deck-canvas" onClick={props.onContextLost}>
        lose the GL context
      </button>
    );
  },
}));

/** WebGL2 (and, in jsdom, 2-D canvas) contexts: null = unavailable. */
function webgl(available: boolean) {
  const context = available
    ? ({ getExtension: () => ({ loseContext: () => {} }) } as unknown as RenderingContext)
    : null;
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(((kind: string) =>
    kind === "webgl2" ? context : null) as typeof HTMLCanvasElement.prototype.getContext);
}

/** The lazy 3-D chunk and the first render are slow on a loaded CI box. */
const SLOW = { timeout: 20_000 };

afterEach(() => {
  resetWebGLProbe();
  canvasProps = undefined;
});

describe("embedding explorer without WebGL", { timeout: 60_000 }, () => {
  it("shows the 2-D projection under a banner when WebGL2 is unavailable", async () => {
    webgl(false);
    render(<ExplorerSection model={fixtureModel()} />);

    const title = await screen.findByText(/3-D view unavailable: this browser has no WebGL2/, {}, SLOW);
    const banner = title.closest('[role="status"]')!;
    expect(banner).not.toBeNull();
    expect(banner).toHaveTextContent(/Showing the 2-D projection/);
    expect(screen.queryByTestId("deck-canvas")).toBeNull();
    const plane = screen.getByRole("img", { name: /projected to 2-D by PCA/ });
    expect(plane.tagName).toBe("CANVAS");
    expect(plane).toHaveAccessibleName(/36 vectors of 384 dimensions/);
    // The non-visual paths stay: legend, inspector hint, query box, reset hidden (no camera).
    expect(screen.getByRole("list", { name: /Categories/ })).toBeInTheDocument();
    expect(screen.getByText(/Hover a point to read its text/)).toBeInTheDocument();
    expect(screen.getByRole("search")).toBeInTheDocument();
  });

  it("swaps to the 2-D view with a banner when the graphics context is lost", async () => {
    webgl(true);
    const user = userEvent.setup();
    render(<ExplorerSection model={fixtureModel()} />);

    const canvas = await screen.findByTestId("deck-canvas", {}, SLOW);
    expect(canvasProps?.ariaLabel).toMatch(/projected to 3-D by PCA/);
    expect(canvasProps?.view).toBe("orbit");
    await user.click(canvas);

    expect(screen.queryByTestId("deck-canvas")).toBeNull();
    expect(screen.getByText(/graphics context was lost/).closest('[role="status"]')).not.toBeNull();
    expect(screen.getByRole("img", { name: /projected to 2-D/ })).toBeInTheDocument();
    // The camera reset belongs to the 3-D view only.
    expect(screen.queryByRole("button", { name: /Reset view/ })).toBeNull();
  });

  it("offers the 2-D view by choice, without a warning, and the toggle writes the URL state", async () => {
    webgl(true);
    const setSearch = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<ExplorerSection model={fixtureModel({ setSearch })} />);
    await screen.findByTestId("deck-canvas", {}, SLOW);

    await user.click(screen.getByRole("radio", { name: "2-D" }));
    expect(setSearch).toHaveBeenCalledWith({ view: "2d" });

    rerender(<ExplorerSection model={fixtureModel({ setSearch, search: { view: "2d" } })} />);
    expect(screen.queryByTestId("deck-canvas")).toBeNull();
    expect(screen.queryByText(/3-D view unavailable|graphics context was lost/)).toBeNull();
    expect(screen.getByRole("img", { name: /projected to 2-D by PCA/ })).toBeInTheDocument();
    expect(screen.getByText(/on a plain canvas, no WebGL/)).toBeInTheDocument();
  });

  it("keeps the table twin and the query working in the fallback", async () => {
    webgl(false);
    const user = userEvent.setup();
    render(<ExplorerSection model={fixtureModel()} />);
    await screen.findByRole("img", { name: /projected to 2-D/ }, SLOW);

    await user.click(screen.getByRole("button", { name: "View data" }));
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row").length).toBeGreaterThan(30);
    expect(within(table).getAllByText("Row document").length).toBe(36);
    expect(within(table).getAllByText(/^#\d$/)).toHaveLength(8);

    await user.type(screen.getByRole("textbox", { name: "Query the space" }), "city is Old Oak,");
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "Embed" }));
    });
    const results = screen.getByRole("list", { name: "Query results" });
    const first = within(results).getAllByRole("button")[0]!;
    expect(first).toHaveTextContent(/city is Old Oak/);
    await user.click(first);
    expect(screen.getByTestId("inspector")).toHaveTextContent(/city is Old Oak/);
  });
});
