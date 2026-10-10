import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

/**
 * Table primitives. The wrapper scrolls horizontally (200-column tables) and is
 * keyboard-focusable so the scroll region is reachable (axe
 * scrollable-region-focusable); give it an `aria-label`.
 */
export function TableContainer({ className, ...props }: ComponentPropsWithRef<"div">) {
  return (
    <div
      tabIndex={0}
      role="region"
      className={cn("relative w-full overflow-auto rounded-md border border-border", className)}
      {...props}
    />
  );
}

export function Table({ className, ...props }: ComponentPropsWithRef<"table">) {
  return <table className={cn("w-full caption-bottom border-collapse text-sm", className)} {...props} />;
}

export function TableHeader({ className, ...props }: ComponentPropsWithRef<"thead">) {
  return <thead className={cn("sticky top-0 z-10 bg-surface-2", className)} {...props} />;
}

export function TableBody({ className, ...props }: ComponentPropsWithRef<"tbody">) {
  return <tbody className={cn("[&_tr:last-child]:border-0", className)} {...props} />;
}

export function TableRow({ className, ...props }: ComponentPropsWithRef<"tr">) {
  return (
    <tr
      className={cn(
        "border-b border-border transition-colors hover:bg-surface-3/60 data-[state=selected]:bg-surface-3",
        className,
      )}
      {...props}
    />
  );
}

export function TableHead({ className, ...props }: ComponentPropsWithRef<"th">) {
  return (
    <th
      className={cn(
        "h-9 px-3 text-left align-middle text-xs font-medium tracking-wide whitespace-nowrap text-text-3",
        className,
      )}
      {...props}
    />
  );
}

export function TableCell({ className, ...props }: ComponentPropsWithRef<"td">) {
  return <td className={cn("px-3 py-2 align-middle text-text-1", className)} {...props} />;
}

export function TableCaption({ className, ...props }: ComponentPropsWithRef<"caption">) {
  return <caption className={cn("px-3 py-2 text-left text-xs text-text-3", className)} {...props} />;
}
