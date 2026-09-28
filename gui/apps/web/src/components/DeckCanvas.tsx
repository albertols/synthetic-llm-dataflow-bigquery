/** Lazy chunk: deck.gl core + React binding. Rendered only by DeckFrame. */
import {
  OrbitView,
  OrthographicView,
  type Deck,
  type DeckProps,
  type Layer,
  type PickingInfo,
  type ViewStateChangeParameters,
} from "@deck.gl/core";
import DeckGL, { type DeckGLRef } from "@deck.gl/react";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { useReducedMotion } from "@/lib/motion";
import { readToken } from "@/lib/theme";

export type DeckCanvasProps = {
  layers: Layer[];
  view: "orbit" | "orthographic";
  initialViewState: object;
  getTooltip?: (info: PickingInfo) => string | null;
  ariaLabel: string;
  hintId: string;
  onContextLost: () => void;
  viewState?: object;
  onViewStateChange?: (params: DeckViewStateChange) => void;
  controller?: DeckProps["controller"];
  onDeckReady?: (deck: Deck) => void;
};

/** A camera change: the next view state (a plain object), the view id and what moved it. */
export type DeckViewStateChange = ViewStateChangeParameters<Record<string, unknown>>;

export default function DeckCanvas({
  layers,
  view,
  initialViewState,
  getTooltip,
  ariaLabel,
  hintId,
  onContextLost,
  viewState,
  onViewStateChange,
  controller: controllerOverride,
  onDeckReady,
}: DeckCanvasProps) {
  const deckRef = useRef<DeckGLRef>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const onLost = useRef(onContextLost);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    onLost.current = onContextLost;
  }, [onContextLost]);

  const views = useMemo(
    () =>
      view === "orbit"
        ? new OrbitView({ id: "orbit", orbitAxis: "Y" })
        : new OrthographicView({ id: "orthographic", flipY: false }),
    [view],
  );

  const defaultController = useMemo(() => ({ keyboard: true, inertia: reducedMotion ? false : 250 }), [reducedMotion]);
  const controller = controllerOverride ?? defaultController;

  const tooltip = useMemo(() => {
    if (!getTooltip) return undefined;
    const style = {
      background: readToken("--surface-2"),
      color: readToken("--text-1"),
      border: `1px solid ${readToken("--border-strong")}`,
      borderRadius: "8px",
      padding: "6px 8px",
      fontFamily: readToken("--font-ui"),
      fontSize: "12px",
      maxWidth: "320px",
      whiteSpace: "pre-wrap",
    };
    return (info: PickingInfo) => {
      const text = getTooltip(info);
      return text ? { text, style } : null;
    };
  }, [getTooltip]);

  // Context loss → hand over to the fallback. On unmount, drop the listener
  // first, let DeckGL finalise, then release the GL context explicitly.
  const handleLost = useCallback((event: Event) => {
    event.preventDefault();
    onLost.current();
  }, []);

  useEffect(
    () => () => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.removeEventListener("webglcontextlost", handleLost);
      window.setTimeout(() => {
        const gl = canvas.getContext("webgl2");
        gl?.getExtension("WEBGL_lose_context")?.loseContext();
      }, 0);
    },
    [handleLost],
  );

  const onLoad = () => {
    const canvas = deckRef.current?.deck?.getCanvas() ?? null;
    if (!canvas) return;
    canvasRef.current = canvas;
    canvas.setAttribute("role", "application");
    canvas.setAttribute("aria-roledescription", view === "orbit" ? "3-D view" : "2-D view");
    canvas.setAttribute("aria-label", ariaLabel);
    canvas.setAttribute("aria-describedby", hintId);
    canvas.tabIndex = 0;
    canvas.addEventListener("webglcontextlost", handleLost);
    const deck = deckRef.current?.deck;
    if (deck) onDeckReady?.(deck);
  };

  return (
    <DeckGL
      ref={deckRef}
      views={views}
      layers={layers}
      {...(viewState ? { viewState } : { initialViewState })}
      onViewStateChange={onViewStateChange}
      controller={controller}
      getTooltip={tooltip}
      onLoad={onLoad}
      style={{ position: "absolute", inset: "0" }}
    />
  );
}
