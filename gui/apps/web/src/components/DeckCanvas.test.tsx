/**
 * DeckCanvas keeps what it writes outside React current: the canvas's
 * accessible name (deck.gl owns the element) and the inline tooltip colours
 * (read from tokens). deck.gl itself is stubbed: jsdom has no WebGL.
 */
import { render } from "@testing-library/react";
import { forwardRef, useEffect, useImperativeHandle, useRef, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemeContext, type ResolvedTheme } from "@/lib/theme";

import DeckCanvas, { type DeckCanvasProps } from "./DeckCanvas";

type Tooltip = (info: { object?: unknown }) => { text: string; style: Record<string, string> } | null;
let lastTooltip: Tooltip | undefined;

vi.mock("@deck.gl/core", () => ({
  OrbitView: class {
    constructor(readonly props: object) {}
  },
  OrthographicView: class {
    constructor(readonly props: object) {}
  },
}));

vi.mock("@deck.gl/react", () => ({
  default: forwardRef(function FakeDeckGL({ onLoad, getTooltip }: { onLoad?: () => void; getTooltip?: Tooltip }, ref) {
    const canvas = useRef<HTMLCanvasElement>(null);
    useImperativeHandle(ref, () => ({ deck: { getCanvas: () => canvas.current } }));
    lastTooltip = getTooltip;
    useEffect(() => onLoad?.(), []); // eslint-disable-line react-hooks/exhaustive-deps -- deck.gl loads once
    return <canvas ref={canvas} data-testid="deck" />;
  }),
}));

function themed(theme: ResolvedTheme, children: ReactNode) {
  return (
    <ThemeContext value={{ preference: theme, resolved: theme, setPreference: () => {} }}>{children}</ThemeContext>
  );
}

const base: DeckCanvasProps = {
  layers: [],
  view: "orbit",
  initialViewState: {},
  ariaLabel: "Row documents in 3-D",
  hintId: "hint-1",
  onContextLost: () => {},
  getTooltip: (info) => (info.object ? "a point" : null),
};

afterEach(() => {
  document.documentElement.style.removeProperty("--surface-2");
  lastTooltip = undefined;
});

describe("DeckCanvas", () => {
  it("renames the canvas when the label or the view changes", () => {
    const { getByTestId, rerender } = render(themed("dark", <DeckCanvas {...base} />));
    const canvas = getByTestId("deck");
    expect(canvas).toHaveAttribute("role", "application");
    expect(canvas).toHaveAttribute("aria-label", "Row documents in 3-D");
    expect(canvas).toHaveAttribute("aria-roledescription", "3-D view");
    expect(canvas).toHaveAttribute("aria-describedby", "hint-1");

    rerender(themed("dark", <DeckCanvas {...base} view="orthographic" ariaLabel="Chunks in 2-D" hintId="hint-2" />));
    expect(canvas).toHaveAttribute("aria-label", "Chunks in 2-D");
    expect(canvas).toHaveAttribute("aria-roledescription", "2-D view");
    expect(canvas).toHaveAttribute("aria-describedby", "hint-2");
  });

  it("re-reads the tooltip colours when the theme flips", () => {
    document.documentElement.style.setProperty("--surface-2", "#181c24");
    const { rerender } = render(themed("dark", <DeckCanvas {...base} />));
    expect(lastTooltip?.({ object: {} })?.style.background).toBe("#181c24");

    document.documentElement.style.setProperty("--surface-2", "#f4f5f7");
    rerender(themed("light", <DeckCanvas {...base} />));
    expect(lastTooltip?.({ object: {} })?.style.background).toBe("#f4f5f7");
    expect(lastTooltip?.({})).toBeNull();
  });
});
