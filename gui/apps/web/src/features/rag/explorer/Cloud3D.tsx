/**
 * The 3-D view: one deck.gl OrbitView canvas (DeckFrame), lazy-loaded with
 * @deck.gl/layers so neither reaches the tab's first chunk.
 *
 * Layers (all pixel-sized, so the cloud reads the same at any zoom):
 * points · seed rings · neighbour lines + focus ring · query lines + point.
 * Positions animate between projections (deck.gl attribute transitions, off
 * under reduced motion). Picking is screen-space nearest-point (usePicking)
 * on the canvas, not deck.gl's pixel picking: a 16 px hit radius around a
 * 6 px mark. While lassoing the camera is frozen (`controller={false}`).
 * WebGL2 missing or lost: DeckFrame swaps in `fallback` (Cloud2D) under its
 * banner and the canvas is gone; route leave releases the GL context.
 */
import type { Deck } from "@deck.gl/core";
import { LineLayer, ScatterplotLayer } from "@deck.gl/layers";
import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";

import { DeckFrame, type DeckViewStateChange } from "@/components/DeckFrame";

import { LassoPath } from "./LassoPath";
import type { CloudViewProps } from "./types";
import { usePicking, type Projector } from "./usePicking";

export interface Cloud3DProps extends CloudViewProps {
  viewState: object;
  onViewStateChange: (next: object) => void;
  onWebglUnavailable: () => void;
  fallback: ReactNode;
}

type Segment = { from: readonly number[]; to: readonly number[] };

export default function Cloud3D(props: Cloud3DProps) {
  const { n, coords, colors, colorVersion, palette, seeds, focus, neighbours, query, reducedMotion, layoutKey } = props;
  const deckRef = useRef<Deck | null>(null);
  const [ready, setReady] = useState(false);

  const indices = useMemo(() => Array.from({ length: n }, (_, i) => i), [n]);
  const position = useCallback(
    (i: number): [number, number, number] => [coords[i * 3]!, coords[i * 3 + 1]!, coords[i * 3 + 2]!],
    [coords],
  );

  const layers = useMemo(() => {
    const transition = reducedMotion ? undefined : { getPosition: { duration: 650 } };
    const lines: Segment[] = [];
    if (focus !== null) for (const j of neighbours) lines.push({ from: position(focus), to: position(j) });
    const queryLines: Segment[] = [];
    if (query?.position) for (const j of query.hits) queryLines.push({ from: query.position, to: position(j) });
    return [
      new ScatterplotLayer<number>({
        id: "points",
        data: indices,
        getPosition: position,
        getFillColor: (i) => [colors[i * 4]!, colors[i * 4 + 1]!, colors[i * 4 + 2]!, colors[i * 4 + 3]!],
        getRadius: 3,
        radiusUnits: "pixels",
        stroked: true,
        getLineColor: [palette.surface[0], palette.surface[1], palette.surface[2], 170],
        getLineWidth: 1,
        lineWidthUnits: "pixels",
        billboard: true,
        antialiasing: true,
        transitions: transition,
        updateTriggers: { getPosition: layoutKey, getFillColor: colorVersion },
        parameters: { depthWriteEnabled: false },
      }),
      new ScatterplotLayer<number>({
        id: "seeds",
        data: [...seeds],
        getPosition: position,
        getRadius: 7,
        radiusUnits: "pixels",
        filled: false,
        stroked: true,
        getLineColor: palette.ring,
        getLineWidth: 2,
        lineWidthUnits: "pixels",
        billboard: true,
        transitions: transition,
        updateTriggers: { getPosition: layoutKey },
        parameters: { depthWriteEnabled: false },
      }),
      new LineLayer<Segment>({
        id: "neighbours",
        data: lines,
        getSourcePosition: (d) => d.from as [number, number, number],
        getTargetPosition: (d) => d.to as [number, number, number],
        getColor: palette.neighbour,
        getWidth: 1.5,
        widthUnits: "pixels",
        parameters: { depthWriteEnabled: false },
      }),
      new LineLayer<Segment>({
        id: "query-lines",
        data: queryLines,
        getSourcePosition: (d) => d.from as [number, number, number],
        getTargetPosition: (d) => d.to as [number, number, number],
        getColor: [palette.accent[0], palette.accent[1], palette.accent[2], 200],
        getWidth: 1.5,
        widthUnits: "pixels",
        parameters: { depthWriteEnabled: false },
      }),
      new ScatterplotLayer<{ p: readonly number[]; r: number; fill: boolean }>({
        id: "marks",
        data: [
          ...(focus !== null ? [{ p: position(focus), r: 9, fill: false }] : []),
          ...(query?.position ? [{ p: query.position, r: 6, fill: true }] : []),
        ],
        getPosition: (d) => d.p as [number, number, number],
        getRadius: (d) => d.r,
        radiusUnits: "pixels",
        filled: true,
        getFillColor: (d) => (d.fill ? palette.accent : [0, 0, 0, 0]),
        stroked: true,
        getLineColor: (d) => (d.fill ? palette.surface : palette.accent),
        getLineWidth: 2,
        lineWidthUnits: "pixels",
        billboard: true,
        parameters: { depthWriteEnabled: false },
      }),
    ];
  }, [indices, position, colors, colorVersion, palette, seeds, focus, neighbours, query, reducedMotion, layoutKey]);

  const project = useCallback<Projector>(
    (i) => {
      const viewport = deckRef.current?.getViewports()[0];
      if (!viewport || i < 0 || i >= n) return null;
      const [x, y] = viewport.project(position(i));
      return x === undefined || y === undefined ? null : [x, y];
    },
    [n, position],
  );

  const { path, handlers } = usePicking({
    n,
    project: ready ? project : null,
    getRect: () => deckRef.current?.getCanvas()?.getBoundingClientRect() ?? null,
    lassoActive: props.lassoActive,
    onHover: props.onHover,
    onSelect: props.onSelect,
    onLasso: props.onLasso,
    // Only the deck canvas: the fallback (Cloud2D) handles its own events.
    accept: (target) => target instanceof HTMLCanvasElement && target === deckRef.current?.getCanvas(),
  });

  return (
    // The pointer handlers watch events bubbling up from the canvas; the canvas itself is the keyboard target.
    <div
      className={props.lassoActive ? "cursor-crosshair touch-none select-none" : undefined}
      data-slot="cloud-3d"
      {...handlers}
    >
      <DeckFrame
        ariaLabel={props.ariaLabel}
        view="orbit"
        initialViewState={props.viewState}
        viewState={props.viewState}
        onViewStateChange={(change: DeckViewStateChange) => props.onViewStateChange(change.viewState)}
        controller={props.lassoActive ? false : undefined}
        onDeckReady={(deck) => {
          deckRef.current = deck;
          setReady(true);
        }}
        onWebglUnavailable={props.onWebglUnavailable}
        layers={layers}
        height={props.height}
        fallback={props.fallback}
      />
      <LassoPath path={path} />
    </div>
  );
}
