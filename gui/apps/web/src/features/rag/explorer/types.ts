import type { CloudPalette } from "./palette";

/** What both views (3-D deck.gl, 2-D canvas) draw and report. */
export interface CloudViewProps {
  n: number;
  /** n × 3, normalised (98th-percentile radius = 1). */
  coords: Float32Array;
  /** n × 4 RGBA bytes. */
  colors: Uint8ClampedArray;
  /** Changes whenever `colors` does (deck.gl update trigger). */
  colorVersion: string;
  palette: CloudPalette;
  /** Seeds of the current strategy (ringed), in pick order. */
  seeds: readonly number[];
  /** The hovered, else the selected, point: ringed, with lines to `neighbours`. */
  focus: number | null;
  neighbours: readonly number[];
  /** The query box: its position (null when it cannot be placed) and its top-k. */
  query: { position: readonly [number, number, number] | null; hits: readonly number[] } | null;
  lassoActive: boolean;
  reducedMotion: boolean;
  height: number;
  ariaLabel: string;
  /** Changes when the projection changes, so positions animate between layouts. */
  layoutKey: string;
  /** Axis names for the 2-D view ("PC 1", "UMAP 1" …). */
  axes: readonly [string, string];
  onHover: (index: number | null, client: { x: number; y: number } | null) => void;
  onSelect: (index: number | null) => void;
  onLasso: (indices: number[]) => void;
}
