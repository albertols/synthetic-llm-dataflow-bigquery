import { ScrollArea as ScrollAreaPrimitive } from "radix-ui";
import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

/**
 * Styled scroll container. The viewport is focusable so keyboard users can
 * scroll it; pass `aria-label` describing the content.
 */
export function ScrollArea({
  className,
  children,
  orientation = "vertical",
  "aria-label": ariaLabel,
  ...props
}: ComponentPropsWithRef<typeof ScrollAreaPrimitive.Root> & { orientation?: "vertical" | "horizontal" | "both" }) {
  return (
    <ScrollAreaPrimitive.Root className={cn("relative overflow-hidden", className)} {...props}>
      <ScrollAreaPrimitive.Viewport
        tabIndex={0}
        aria-label={ariaLabel}
        className="size-full rounded-[inherit] focus-visible:outline-2 focus-visible:outline-focus-ring"
      >
        {children}
      </ScrollAreaPrimitive.Viewport>
      {orientation !== "horizontal" ? <ScrollBar orientation="vertical" /> : null}
      {orientation !== "vertical" ? <ScrollBar orientation="horizontal" /> : null}
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  );
}

export function ScrollBar({
  className,
  orientation = "vertical",
  ...props
}: ComponentPropsWithRef<typeof ScrollAreaPrimitive.Scrollbar>) {
  return (
    <ScrollAreaPrimitive.Scrollbar
      orientation={orientation}
      className={cn(
        "flex touch-none p-0.5 transition-colors select-none",
        orientation === "vertical" ? "h-full w-2.5" : "h-2.5 flex-col",
        className,
      )}
      {...props}
    >
      <ScrollAreaPrimitive.Thumb className="relative flex-1 rounded-full bg-border-strong hover:bg-control-border" />
    </ScrollAreaPrimitive.Scrollbar>
  );
}
