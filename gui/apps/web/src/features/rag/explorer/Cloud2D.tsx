/**
 * The 2-D view of the cloud: the first two projected axes on a Canvas 2D
 * surface. It is the WebGL fallback (DeckFrame renders it under its banner
 * when WebGL2 is missing or the context is lost) and the "2-D" choice of the
 * view toggle. No WebGL, one canvas, redrawn on change; hover, click and
 * lasso work exactly as in 3-D (usePicking).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { readToken } from "@/lib/theme";

import { LassoPath } from "./LassoPath";
import { css } from "./palette";
import type { CloudViewProps } from "./types";
import { usePicking, type Projector } from "./usePicking";

/** World units shown across the shorter side: the normalised cloud (radius ≈ 1) with a margin. */
const SPAN = 2.3;

export function Cloud2D(props: CloudViewProps) {
  const { n, coords, colors, palette, seeds, focus, neighbours, query, height, ariaLabel, axes } = props;
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    const box = canvas?.parentElement;
    if (!box || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => setWidth(Math.round(entries[0]?.contentRect.width ?? 0)));
    observer.observe(box);
    return () => observer.disconnect();
  }, []);

  const scale = Math.min(width || 1, height) / SPAN;
  const project = useCallback<Projector>(
    (i) => {
      if (i < 0 || i >= n || !width) return null;
      return [width / 2 + coords[i * 3]! * scale, height / 2 - coords[i * 3 + 1]! * scale];
    },
    [n, width, height, coords, scale],
  );

  const { path, handlers } = usePicking({
    n,
    project: width ? project : null,
    getRect: () => canvasRef.current?.getBoundingClientRect() ?? null,
    lassoActive: props.lassoActive,
    onHover: props.onHover,
    onSelect: props.onSelect,
    onLasso: props.onLasso,
  });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !width) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = readToken("--chart-surface", "#12151b");
    ctx.fillRect(0, 0, width, height);
    // Axes through the origin: hairline, one step off the surface.
    ctx.strokeStyle = readToken("--chart-grid", "#262b35");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, height / 2 + 0.5);
    ctx.lineTo(width, height / 2 + 0.5);
    ctx.moveTo(width / 2 + 0.5, 0);
    ctx.lineTo(width / 2 + 0.5, height);
    ctx.stroke();
    ctx.fillStyle = readToken("--text-3", "#939aa7");
    ctx.font = `12px ${readToken("--font-ui", "sans-serif")}`;
    ctx.textAlign = "right";
    ctx.fillText(`${axes[0]} →`, width - 8, height / 2 - 6);
    ctx.textAlign = "left";
    ctx.fillText(`↑ ${axes[1]}`, width / 2 + 6, 16);

    const at = (i: number) => project(i);
    const surface = css(palette.surface);
    // Dimmed first, then full-alpha points on top; a 1 px surface ring keeps overlaps legible.
    for (const pass of [0, 1]) {
      for (let i = 0; i < n; i += 1) {
        const alpha = colors[i * 4 + 3]!;
        if ((pass === 0) !== alpha < 128) continue;
        const p = at(i);
        if (!p) continue;
        ctx.beginPath();
        ctx.arc(p[0], p[1], 3, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${colors[i * 4]}, ${colors[i * 4 + 1]}, ${colors[i * 4 + 2]}, ${alpha / 255})`;
        ctx.fill();
        if (pass === 1) {
          ctx.strokeStyle = surface;
          ctx.lineWidth = 1;
          ctx.stroke();
        }
      }
    }
    const line = (from: readonly number[], to: readonly number[], color: string, widthPx: number) => {
      ctx.beginPath();
      ctx.moveTo(from[0]!, from[1]!);
      ctx.lineTo(to[0]!, to[1]!);
      ctx.strokeStyle = color;
      ctx.lineWidth = widthPx;
      ctx.stroke();
    };
    const ring = (p: readonly number[], radius: number, color: string, widthPx: number) => {
      ctx.beginPath();
      ctx.arc(p[0]!, p[1]!, radius, 0, Math.PI * 2);
      ctx.strokeStyle = color;
      ctx.lineWidth = widthPx;
      ctx.stroke();
    };
    for (const s of seeds) {
      const p = at(s);
      if (p) ring(p, 7, css(palette.ring), 2);
    }
    const f = focus !== null ? at(focus) : null;
    if (f) {
      for (const j of neighbours) {
        const p = at(j);
        if (p) line(f, p, css(palette.neighbour), 1.5);
      }
      ring(f, 9, css(palette.accent), 2);
    }
    if (query?.position) {
      const q: [number, number] = [width / 2 + query.position[0] * scale, height / 2 - query.position[1] * scale];
      for (const j of query.hits) {
        const p = at(j);
        if (p) line(q, p, css(palette.accent, 200), 1.5);
      }
      ctx.beginPath();
      ctx.arc(q[0], q[1], 6, 0, Math.PI * 2);
      ctx.fillStyle = css(palette.accent);
      ctx.fill();
      ctx.strokeStyle = surface;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }, [n, colors, palette, seeds, focus, neighbours, query, width, height, project, scale, axes]);

  return (
    <div className="relative w-full" style={{ height }} data-slot="cloud-2d">
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={ariaLabel}
        className={props.lassoActive ? "block cursor-crosshair touch-none select-none" : "block cursor-default"}
        style={{ width: "100%", height }}
        {...handlers}
      />
      <LassoPath path={path} />
    </div>
  );
}
