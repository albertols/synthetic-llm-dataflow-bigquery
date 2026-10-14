import { ToggleGroup as ToggleGroupPrimitive } from "radix-ui";
import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

/** Segmented control (e.g. PCA | UMAP, colour-by). Label the group with `aria-label`. */
export function ToggleGroup({ className, ...props }: ComponentPropsWithRef<typeof ToggleGroupPrimitive.Root>) {
  return (
    <ToggleGroupPrimitive.Root
      className={cn("inline-flex items-center gap-0.5 rounded-md border border-border bg-surface-1 p-0.5", className)}
      {...props}
    />
  );
}

export function ToggleGroupItem({ className, ...props }: ComponentPropsWithRef<typeof ToggleGroupPrimitive.Item>) {
  return (
    <ToggleGroupPrimitive.Item
      className={cn(
        "inline-flex h-8 cursor-pointer items-center justify-center gap-1.5 rounded-sm px-3 text-sm font-medium text-text-2",
        "transition-colors hover:bg-surface-3 hover:text-text-1 disabled:pointer-events-none disabled:opacity-50",
        "data-[state=on]:bg-surface-3 data-[state=on]:text-text-1 data-[state=on]:shadow-[inset_0_-2px_0_var(--accent)] [&_svg]:size-4",
        className,
      )}
      {...props}
    />
  );
}
