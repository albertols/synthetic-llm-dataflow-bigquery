/**
 * Pointer picking shared by the 3-D and 2-D views, in screen space: the
 * nearest point within HIT_RADIUS px is the hovered one (a hit target far
 * larger than the 6 px mark, the dataviz nearest-point rule), a press without
 * a drag selects it, and in lasso mode a drag draws a polygon whose points
 * are selected on release.
 *
 * `project(i)` maps point i to CSS pixels relative to `getRect()` (the
 * drawing surface); the lasso path is kept in client coordinates so it can be
 * drawn in a fixed overlay whatever the surface's layout.
 */
import { useCallback, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

export const HIT_RADIUS = 16;
const CLICK_SLOP = 5;

export type Projector = (i: number) => readonly [number, number] | null;

export interface PickingOptions {
  n: number;
  project: Projector | null;
  getRect: () => DOMRect | null;
  lassoActive: boolean;
  onHover: (index: number | null, client: { x: number; y: number } | null) => void;
  onSelect: (index: number | null) => void;
  onLasso: (indices: number[]) => void;
  /** Only events whose target passes are handled (the 3-D wrapper ignores its fallback's events). */
  accept?: (target: EventTarget | null) => boolean;
}

/** Ray casting: is (x, y) inside the polygon? */
export function insidePolygon(x: number, y: number, polygon: readonly (readonly [number, number])[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i]!;
    const [xj, yj] = polygon[j]!;
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function nearestPoint(n: number, project: Projector, x: number, y: number, radius = HIT_RADIUS): number | null {
  let best: number | null = null;
  let bestD = radius * radius;
  for (let i = 0; i < n; i += 1) {
    const p = project(i);
    if (!p) continue;
    const d = (p[0] - x) ** 2 + (p[1] - y) ** 2;
    if (d <= bestD) {
      bestD = d;
      best = i;
    }
  }
  return best;
}

function local(options: PickingOptions, event: ReactPointerEvent) {
  const rect = options.getRect();
  return rect ? { x: event.clientX - rect.left, y: event.clientY - rect.top } : null;
}

export function usePicking(options: PickingOptions) {
  const latest = useRef(options);
  useLayoutEffect(() => {
    latest.current = options;
  });
  const [path, setPath] = useState<[number, number][] | null>(null);
  const down = useRef<{ x: number; y: number; id: number } | null>(null);
  const drawing = useRef<[number, number][] | null>(null);
  const frame = useRef<number | null>(null);

  const onPointerMove = useCallback((event: ReactPointerEvent) => {
    const o = latest.current;
    if (o.accept && !o.accept(event.target)) return;
    if (drawing.current) {
      drawing.current.push([event.clientX, event.clientY]);
      setPath([...drawing.current]);
      return;
    }
    if (event.pointerType === "touch" && down.current) return;
    const point = local(o, event);
    const client = { x: event.clientX, y: event.clientY };
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      const current = latest.current;
      if (!point || !current.project) return current.onHover(null, null);
      const hit = nearestPoint(current.n, current.project, point.x, point.y);
      current.onHover(hit, hit === null ? null : client);
    });
  }, []);

  const onPointerDown = useCallback((event: ReactPointerEvent) => {
    const o = latest.current;
    if (o.accept && !o.accept(event.target)) return;
    down.current = { x: event.clientX, y: event.clientY, id: event.pointerId };
    if (o.lassoActive) {
      drawing.current = [[event.clientX, event.clientY]];
      setPath([...drawing.current]);
      (event.target as Element).setPointerCapture?.(event.pointerId);
    }
  }, []);

  const onPointerUp = useCallback((event: ReactPointerEvent) => {
    const o = latest.current;
    const start = down.current;
    down.current = null;
    if (drawing.current) {
      const polygon = drawing.current;
      drawing.current = null;
      setPath(null);
      const rect = o.getRect();
      if (polygon.length >= 3 && rect && o.project) {
        const localPolygon = polygon.map(([x, y]) => [x - rect.left, y - rect.top] as const);
        const picked: number[] = [];
        for (let i = 0; i < o.n; i += 1) {
          const p = o.project(i);
          if (p && insidePolygon(p[0], p[1], localPolygon)) picked.push(i);
        }
        o.onLasso(picked);
      }
      return;
    }
    if (o.accept && !o.accept(event.target)) return;
    if (!start || Math.hypot(event.clientX - start.x, event.clientY - start.y) > CLICK_SLOP) return;
    const point = local(o, event);
    if (!point || !o.project) return;
    o.onSelect(nearestPoint(o.n, o.project, point.x, point.y));
  }, []);

  const onPointerLeave = useCallback(() => {
    if (!drawing.current) latest.current.onHover(null, null);
  }, []);

  return { path, handlers: { onPointerMove, onPointerDown, onPointerUp, onPointerLeave } };
}
