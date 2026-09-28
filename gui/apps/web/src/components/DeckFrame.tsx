/**
 * DeckFrame — the one way to put a deck.gl (WebGL2) view on screen.
 *
 *   <DeckFrame
 *     ariaLabel="Row-document embeddings projected to 3-D (PCA)"
 *     view="orbit"
 *     initialViewState={{ target: [0, 0, 0], rotationX: 30, rotationOrbit: 30, zoom: 5 }}
 *     layers={[new ScatterplotLayer({ id: "points", data, getPosition: (d) => d.xyz, … })]}
 *     getTooltip={(info) => (info.object ? info.object.label : null)}
 *     onWebglUnavailable={() => setMode("2d")}
 *     fallback={<Projection2D … />}
 *   />
 *
 * - deck.gl is a lazy chunk; one canvas per frame; finalised and its GL
 *   context released on unmount (route leave).
 * - WebGL2 missing, or the context lost later (Intel iGPU reset, background
 *   tab): `onWebglUnavailable` fires once, a live warning banner says why, and
 *   `fallback` (your 2-D view) renders under it.
 * - Keyboard: the canvas is focusable; arrows pan, Shift + arrows orbit,
 *   + / − zoom. Inertia is off under prefers-reduced-motion.
 * - Linked views and lasso: pass `viewState` + `onViewStateChange` to control
 *   the camera, `controller={{ dragRotate: false, dragPan: false }}` (or false)
 *   while a lasso is drawn, and use the Deck from `onDeckReady` for
 *   `pickObjects({ x, y, width, height })` / `pickObjectsAsync`.
 * - Keep a table or 2-D twin of the data elsewhere on the page (WCAG).
 */
import type { Deck, DeckProps, Layer, PickingInfo } from "@deck.gl/core";
import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/cn";
import { isWebGL2Available } from "@/lib/webgl";

import { Banner } from "./Callout";
import type { DeckViewStateChange } from "./DeckCanvas";
import { Skeleton } from "./ui/skeleton";

const DeckCanvas = lazy(() => import("./DeckCanvas"));

export type { DeckViewStateChange } from "./DeckCanvas";

export type DeckFrameProps = {
  layers: Layer[];
  view: "orbit" | "orthographic";
  /** deck.gl view state for the chosen view (OrbitViewState / OrthographicViewState). */
  initialViewState: object;
  /** Called once when WebGL2 is missing or the context is lost. */
  onWebglUnavailable?: () => void;
  /** Tooltip text for a picked object; null hides the tooltip. */
  getTooltip?: (info: PickingInfo) => string | null;
  /** What the view shows; names the canvas for screen readers. */
  ariaLabel: string;
  /** Canvas height in px (default 420). */
  height?: number;
  /** Rendered (under a warning banner) instead of the canvas when WebGL is unavailable or lost. */
  fallback?: ReactNode;
  /** Controlled camera: pass it with `onViewStateChange`; `initialViewState` is then ignored. */
  viewState?: object;
  /** Every camera change (drag, zoom, keyboard); return nothing. */
  onViewStateChange?: (params: DeckViewStateChange) => void;
  /** Overrides the controller (default: keyboard on, inertia unless reduced motion); false freezes the camera. */
  controller?: DeckProps["controller"];
  /** The Deck instance once loaded: pickObjects, pickObjectsAsync, getViewports … */
  onDeckReady?: (deck: Deck) => void;
  className?: string;
};

export function DeckFrame({
  layers,
  view,
  initialViewState,
  onWebglUnavailable,
  getTooltip,
  ariaLabel,
  height = 420,
  fallback,
  viewState,
  onViewStateChange,
  controller,
  onDeckReady,
  className,
}: DeckFrameProps) {
  const [available] = useState(isWebGL2Available);
  const [lost, setLost] = useState(false);
  const hintId = useId();
  const notified = useRef(false);
  const onUnavailable = useRef(onWebglUnavailable);
  useEffect(() => {
    onUnavailable.current = onWebglUnavailable;
  }, [onWebglUnavailable]);

  const notify = useCallback(() => {
    if (notified.current) return;
    notified.current = true;
    onUnavailable.current?.();
  }, []);

  useEffect(() => {
    if (!available) notify();
  }, [available, notify]);

  const handleContextLost = useCallback(() => {
    setLost(true);
    notify();
  }, [notify]);

  if (!available || lost) {
    return (
      <div className={cn("grid gap-3", className)} data-slot="deck-fallback">
        <Banner
          tone="warn"
          title={
            lost
              ? "The 3-D view stopped: the graphics context was lost"
              : "3-D view unavailable: this browser has no WebGL2"
          }
        >
          {fallback
            ? "Showing the 2-D projection of the same data instead."
            : "The same data is available in the 2-D view and the table."}
        </Banner>
        {fallback}
      </div>
    );
  }

  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn("relative overflow-hidden rounded-lg border border-border bg-surface-1", className)}
      data-slot="deck-frame"
    >
      <div style={{ height }} className="relative">
        <Suspense fallback={<Skeleton style={{ height }} className="w-full rounded-none" />}>
          <DeckCanvas
            layers={layers}
            view={view}
            initialViewState={initialViewState}
            getTooltip={getTooltip}
            ariaLabel={ariaLabel}
            hintId={hintId}
            onContextLost={handleContextLost}
            viewState={viewState}
            onViewStateChange={onViewStateChange}
            controller={controller}
            onDeckReady={onDeckReady}
          />
        </Suspense>
      </div>
      <p id={hintId} className="border-t border-border px-3 py-1.5 text-xs text-text-3">
        Drag to {view === "orbit" ? "orbit" : "pan"}, scroll to zoom. Keyboard: focus the view, then arrows pan
        {view === "orbit" ? ", Shift + arrows orbit" : ""}, + / − zoom.
      </p>
    </div>
  );
}
