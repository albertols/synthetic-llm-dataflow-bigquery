import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { resetWebGLProbe } from "@/lib/webgl";

import { DeckFrame } from "./DeckFrame";

vi.mock("./DeckCanvas", () => ({ default: () => <canvas data-testid="deck-canvas" /> }));

describe("DeckFrame", () => {
  afterEach(() => resetWebGLProbe());

  it("falls back and notifies once when WebGL2 is unavailable", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const onWebglUnavailable = vi.fn();
    render(
      <DeckFrame
        ariaLabel="Embeddings"
        view="orbit"
        initialViewState={{}}
        layers={[]}
        onWebglUnavailable={onWebglUnavailable}
      />,
    );

    expect(screen.getByText(/this browser has no WebGL2/)).toBeInTheDocument();
    expect(screen.queryByTestId("deck-canvas")).toBeNull();
    expect(onWebglUnavailable).toHaveBeenCalledTimes(1);
  });

  it("renders a caller-supplied 2-D fallback", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    render(
      <DeckFrame
        ariaLabel="Embeddings"
        view="orbit"
        initialViewState={{}}
        layers={[]}
        fallback={<p>2-D projection</p>}
      />,
    );

    expect(screen.getByText("2-D projection")).toBeInTheDocument();
  });

  it("mounts the canvas inside a labelled group with keyboard help when WebGL2 exists", async () => {
    const fakeContext = { getExtension: () => ({ loseContext: () => {} }) } as unknown as RenderingContext;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeContext);
    render(<DeckFrame ariaLabel="Embeddings" view="orbit" initialViewState={{}} layers={[]} />);

    expect(screen.getByRole("group", { name: "Embeddings" })).toBeInTheDocument();
    expect(await screen.findByTestId("deck-canvas")).toBeInTheDocument();
    expect(screen.getByText(/Shift \+ arrows orbit/)).toBeInTheDocument();
  });
});
