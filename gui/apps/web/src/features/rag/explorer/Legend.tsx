/**
 * The cloud's legend: every category with its swatch and count (identity is
 * never colour-alone: the label is always there), the "Other" fold, and the
 * mark key (seed ring, focus ring, query dot). A category button isolates it
 * (emphasis form: that category stays bright, the rest dims); the pressed
 * state is announced.
 */
import { formatCount } from "@/lib/format";
import { cn } from "@/lib/cn";

import type { Category } from "../lib/categories";
import { css, type CloudPalette } from "./palette";

export function Legend({
  categories,
  palette,
  isolated,
  onIsolate,
  showSeeds,
  showQuery,
}: {
  categories: readonly Category[];
  palette: CloudPalette;
  isolated: string | null;
  onIsolate: (key: string | null) => void;
  showSeeds: boolean;
  showQuery: boolean;
}) {
  const folded = categories.filter((c) => c.slot === null);
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-text-2">
      <ul className="flex flex-wrap items-center gap-1.5" aria-label="Categories (press to isolate one)">
        {categories.map((c) => {
          const color = c.slot !== null ? palette.slots[c.slot]! : palette.other;
          const pressed = isolated === c.key;
          return (
            <li key={c.key}>
              <button
                type="button"
                aria-pressed={pressed}
                onClick={() => onIsolate(pressed ? null : c.key)}
                className={cn(
                  "inline-flex min-h-8 items-center gap-1.5 rounded-full border px-2.5 py-1 transition-colors",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring",
                  pressed
                    ? "border-accent bg-accent-soft text-text-1"
                    : "border-border hover:border-control-border hover:text-text-1",
                  isolated && !pressed && "opacity-70",
                )}
              >
                <span
                  aria-hidden="true"
                  className="inline-block size-2.5 rounded-full"
                  style={{ background: css(color, 255) }}
                />
                <span>{c.label}</span>
                <span className="text-text-3 tabular-nums">{formatCount(c.count)}</span>
                {c.slot === null ? <span className="sr-only">(drawn as Other)</span> : null}
              </button>
            </li>
          );
        })}
      </ul>
      {folded.length ? (
        <span className="text-text-3">
          {folded.length} categor{folded.length === 1 ? "y is" : "ies are"} drawn in grey (“Other”): a point cloud keeps
          three hues so every pair stays distinguishable.
        </span>
      ) : null}
      <span className="inline-flex items-center gap-3 text-text-3" aria-label="Marks">
        {showSeeds ? (
          <span className="inline-flex items-center gap-1.5">
            <svg width="16" height="16" aria-hidden="true">
              <circle cx="8" cy="8" r="6" fill="none" stroke={css(palette.ring)} strokeWidth="2" />
            </svg>
            seed
          </span>
        ) : null}
        <span className="inline-flex items-center gap-1.5">
          <svg width="16" height="16" aria-hidden="true">
            <circle cx="8" cy="8" r="6" fill="none" stroke={css(palette.accent)} strokeWidth="2" />
          </svg>
          hovered / selected
        </span>
        {showQuery ? (
          <span className="inline-flex items-center gap-1.5">
            <svg width="16" height="16" aria-hidden="true">
              <circle cx="8" cy="8" r="5" fill={css(palette.accent)} />
            </svg>
            query
          </span>
        ) : null}
      </span>
    </div>
  );
}
