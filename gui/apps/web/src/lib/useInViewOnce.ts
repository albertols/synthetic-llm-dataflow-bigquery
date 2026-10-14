import { useEffect, useRef, useState, type RefObject } from "react";

/**
 * True once the element has come within `margin` of the viewport, then stays
 * true. Heavy lazy pieces (mermaid, chart grids) wait for it; without
 * IntersectionObserver (tests, old browsers) it is true at once.
 *
 *   const [ref, seen] = useInViewOnce<HTMLDivElement>();
 *   return <div ref={ref}>{seen ? <Mermaid … /> : <Skeleton … />}</div>;
 */
export function useInViewOnce<T extends Element>(margin = "600px"): [RefObject<T | null>, boolean] {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === "undefined");
  useEffect(() => {
    const element = ref.current;
    if (seen || !element) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { rootMargin: margin },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [seen, margin]);
  return [ref, seen];
}
