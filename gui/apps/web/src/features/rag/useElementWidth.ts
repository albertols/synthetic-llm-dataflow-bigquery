import { useEffect, useState, type RefObject } from "react";

/** The element's content width, kept current by a ResizeObserver (0 until measured). */
export function useElementWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => setWidth(Math.round(entries[0]?.contentRect.width ?? 0)));
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}
