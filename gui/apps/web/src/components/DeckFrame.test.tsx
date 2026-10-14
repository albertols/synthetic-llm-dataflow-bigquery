import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { resetWebGLProbe } from "@/lib/webgl";

import type { DeckCanvasProps } from "./DeckCanvas";
import { DeckFrame } from "./DeckFrame";

let canvasProps: DeckCanvasProps | undefined;
vi.mock("./DeckCanvas", () => ({
  default: (props: DeckCanvasProps) => {
    canvasProps = props;
    return (
      <button type="button" data-testid="deck-canvas" onClick={props.onContextLost}>
        lose the GL context
      </button>
    );
  },
}));

function withWebGL2() {
  const fakeContext = { getExtension: () => ({ loseContext: () => {} }) } as unknown as RenderingContext;
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeContext);
}

describe("DeckFrame", () => {
  afterEach(() => {
    resetWebGLProbe();
    canvasProps = undefined;
  });

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

    expect(screen.getByRole("status")).toHaveTextContent(/this browser has no WebGL2/);
    expect(screen.queryByTestId("deck-canvas")).toBeNull();
    expect(onWebglUnavailable).toHaveBeenCalledTimes(1);
  });

  it("renders a caller-supplied 2-D fallback under the banner", () => {
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
    expect(screen.getByRole("status")).toHaveTextContent(/Showing the 2-D projection/);
  });

  it("mounts the canvas inside a labelled group with keyboard help when WebGL2 exists", async () => {
    withWebGL2();
    render(<DeckFrame ariaLabel="Embeddings" view="orbit" initialViewState={{}} layers={[]} />);

    expect(screen.getByRole("group", { name: "Embeddings" })).toBeInTheDocument();
    expect(await screen.findByTestId("deck-canvas")).toBeInTheDocument();
    expect(screen.getByText(/Shift \+ arrows orbit/)).toBeInTheDocument();
  });

  it("swaps to the fallback with a banner, and notifies once, when the context is lost", async () => {
    withWebGL2();
    const user = userEvent.setup();
    const onWebglUnavailable = vi.fn();
    render(
      <DeckFrame
        ariaLabel="Embeddings"
        view="orbit"
        initialViewState={{}}
        layers={[]}
        onWebglUnavailable={onWebglUnavailable}
        fallback={<p>2-D projection</p>}
      />,
    );

    await user.click(await screen.findByTestId("deck-canvas"));

    expect(screen.queryByTestId("deck-canvas")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(/graphics context was lost/);
    expect(screen.getByText("2-D projection")).toBeInTheDocument();
    expect(onWebglUnavailable).toHaveBeenCalledTimes(1);
  });

  it("passes the controlled camera, the controller override and onDeckReady to the canvas", async () => {
    withWebGL2();
    const viewState = { target: [0, 0, 0], zoom: 3 };
    const onViewStateChange = vi.fn();
    const onDeckReady = vi.fn();
    render(
      <DeckFrame
        ariaLabel="Embeddings"
        view="orthographic"
        initialViewState={{}}
        layers={[]}
        viewState={viewState}
        onViewStateChange={onViewStateChange}
        controller={false}
        onDeckReady={onDeckReady}
      />,
    );

    await screen.findByTestId("deck-canvas");
    expect(canvasProps).toMatchObject({
      viewState,
      onViewStateChange,
      controller: false,
      onDeckReady,
      view: "orthographic",
    });
  });
});
