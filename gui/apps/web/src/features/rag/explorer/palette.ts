/**
 * Colours of the point cloud, read from the design tokens (re-read on a theme
 * change): categorical slots 1–3, "Other", and the reserved marks — seeds
 * wear a primary-text ring, the hovered/selected point and the query wear the
 * accent. Dimmed points (outside the lasso, or not the isolated category)
 * keep their hue at low alpha, so the eye still sees the whole cloud.
 */
import { tokenRgba, type RGBA } from "@/lib/color";

import type { Categorised } from "../lib/categories";

export interface CloudPalette {
  slots: RGBA[];
  other: RGBA;
  accent: RGBA;
  ring: RGBA;
  neighbour: RGBA;
  surface: RGBA;
}

export function readPalette(): CloudPalette {
  return {
    slots: [tokenRgba("--chart-1"), tokenRgba("--chart-2"), tokenRgba("--chart-3")],
    other: tokenRgba("--chart-other"),
    accent: tokenRgba("--accent"),
    ring: tokenRgba("--text-1"),
    neighbour: tokenRgba("--text-2", 190),
    surface: tokenRgba("--surface-1"),
  };
}

export const ALPHA = 225;
export const ALPHA_DIM = 38;

/** n × 4 RGBA bytes. `isolated` (a category key) keeps full alpha on that category only; `keep` does the same for a lasso. */
export function pointColors(
  categorised: Categorised,
  palette: CloudPalette,
  options: { isolated?: string | null; keep?: ReadonlySet<number> | null } = {},
): Uint8ClampedArray {
  const n = categorised.of.length;
  const out = new Uint8ClampedArray(n * 4);
  const isolatedIndex = options.isolated ? categorised.categories.findIndex((c) => c.key === options.isolated) : -1;
  for (let i = 0; i < n; i += 1) {
    const c = categorised.of[i]!;
    const category = categorised.categories[c];
    let color = category && category.slot !== null ? palette.slots[category.slot]! : palette.other;
    if (isolatedIndex >= 0 && c === isolatedIndex && category?.slot === null) color = palette.accent;
    const dim = (isolatedIndex >= 0 && c !== isolatedIndex) || (options.keep ? !options.keep.has(i) : false);
    out[i * 4] = color[0];
    out[i * 4 + 1] = color[1];
    out[i * 4 + 2] = color[2];
    out[i * 4 + 3] = dim ? ALPHA_DIM : ALPHA;
  }
  return out;
}

export function css([r, g, b, a]: readonly number[], alpha?: number): string {
  return `rgba(${r}, ${g}, ${b}, ${((alpha ?? a ?? 255) / 255).toFixed(3)})`;
}
