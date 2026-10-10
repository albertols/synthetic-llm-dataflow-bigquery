import { cn } from "@/lib/cn";

/** Synthetic Platform's mark: a real sample (solid) and its synthetic twin (dashed). Original artwork. */
export function ProductMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false" className={cn("size-7", className)}>
      <rect width="32" height="32" rx="8" fill="var(--surface-3)" />
      <circle cx="13" cy="16" r="7" fill="var(--accent)" />
      <circle cx="19" cy="16" r="7" fill="none" stroke="var(--link)" strokeWidth="2.25" strokeDasharray="3.2 2.2" />
    </svg>
  );
}
