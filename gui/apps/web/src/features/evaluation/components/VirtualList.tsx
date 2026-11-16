/**
 * VirtualList — a list that renders only its visible rows, so a 100+ table launch keeps its
 * per-table scorecards cheap (gui/docs/ARCHITECTURE.md, evaluation tab; ADR 0036). Up to `rows`
 * items it is a plain list; beyond, a fixed-height, keyboard-focusable scroll frame (axe
 * scrollable-region-focusable) holds a list of `items.length` rows of `rowHeight` px of which only
 * the window plus `overscan` rows are in the DOM, each with aria-setsize / aria-posinset so a screen
 * reader knows the whole length. Hand-built: no virtualization library is a dependency.
 *
 * The scroll offset is state, clamped at render (a filter that shortens the list never leaves the
 * window past its end); a `scrollToIndex` change adjusts that state during render, and the effect
 * only copies it to the DOM, so no effect body calls setState. A row that holds focus stays rendered
 * when it scrolls out of the window (tracked by the rows' focus and blur events), so focus never
 * drops to `<body>`.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/cn";

/** Rows shown before a list scrolls. */
export const VIRTUAL_ROWS = 12;

/** The [start, end) slice to render at a scroll offset. */
export function visibleRange(
  scrollTop: number,
  viewport: number,
  rowHeight: number,
  count: number,
  overscan: number,
): { start: number; end: number } {
  if (count <= 0 || rowHeight <= 0) return { start: 0, end: 0 };
  const first = Math.floor(Math.max(0, scrollTop) / rowHeight);
  const visible = Math.ceil(viewport / rowHeight) + 1;
  return { start: Math.max(0, first - overscan), end: Math.min(count, first + visible + overscan) };
}

export interface VirtualListProps<T> {
  items: readonly T[];
  /** Every row's height, px. */
  rowHeight: number;
  /** The list's accessible name. */
  label: string;
  /** The scroll frame's accessible name (default `<label> (<n>, scrolls)`). */
  frameLabel?: string;
  getKey: (item: T) => string;
  renderItem: (item: T, index: number) => ReactNode;
  rows?: number;
  overscan?: number;
  /** Keep this row in view (the selected table). */
  scrollToIndex?: number;
  className?: string;
}

export function VirtualList<T>({
  items,
  rowHeight,
  label,
  frameLabel,
  getKey,
  renderItem,
  rows = VIRTUAL_ROWS,
  overscan = 4,
  scrollToIndex,
  className,
}: VirtualListProps<T>) {
  const frame = useRef<HTMLDivElement>(null);
  const [scrolled, setScrolled] = useState(0);
  const [followed, setFollowed] = useState<number | undefined>(undefined);
  // The key of the row that holds focus: it stays rendered outside the window.
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const windowed = items.length > rows;
  const viewport = rows * rowHeight;
  const height = items.length * rowHeight;
  const maxTop = Math.max(0, height - viewport);
  const scrollTop = Math.min(scrolled, maxTop);

  // A new row to follow: move the offset so it is in view (adjusting state during render).
  if (followed !== scrollToIndex) {
    setFollowed(scrollToIndex);
    if (scrollToIndex !== undefined && scrollToIndex >= 0) {
      const top = scrollToIndex * rowHeight;
      if (top < scrollTop || top + rowHeight > scrollTop + viewport) {
        setScrolled(Math.min(maxTop, Math.max(0, top - rowHeight)));
      }
    }
  }

  useEffect(() => {
    const el = frame.current;
    if (windowed && el && Math.abs(el.scrollTop - scrollTop) >= 1) el.scrollTop = scrollTop;
  }, [windowed, scrollTop]);

  if (!windowed)
    return (
      <ul aria-label={label} className={cn("m-0 grid list-none p-0", className)}>
        {items.map((item, index) => (
          <li key={getKey(item)}>{renderItem(item, index)}</li>
        ))}
      </ul>
    );
  const { start, end } = visibleRange(scrollTop, viewport, rowHeight, items.length, overscan);
  const indices = Array.from({ length: end - start }, (_, k) => start + k);
  const focused = focusedKey === null ? -1 : items.findIndex((item) => getKey(item) === focusedKey);
  // In index order, so the DOM (and the tab order) stays the list's order.
  if (focused >= 0 && (focused < start || focused >= end))
    indices.splice(focused < start ? 0 : indices.length, 0, focused);
  return (
    <div
      ref={frame}
      role="region"
      tabIndex={0}
      aria-label={frameLabel ?? `${label} (${items.length}, scrolls)`}
      onScroll={(event) => setScrolled(event.currentTarget.scrollTop)}
      className="relative overflow-y-auto rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
      style={{ height: viewport }}
    >
      <ul aria-label={label} className={cn("relative m-0 list-none p-0", className)} style={{ height }}>
        {indices.map((index) => {
          const item = items[index]!;
          const key = getKey(item);
          return (
            <li
              key={key}
              aria-setsize={items.length}
              aria-posinset={index + 1}
              className="absolute inset-x-0"
              style={{ top: index * rowHeight, height: rowHeight }}
              // React's focus events also bubble from a portal the row owns (an open popover), so the row
              // stays while focus is anywhere in it.
              onFocus={() => setFocusedKey(key)}
              onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget)) setFocusedKey(null);
              }}
            >
              {renderItem(item, index)}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
