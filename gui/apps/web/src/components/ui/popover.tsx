import { Popover as PopoverPrimitive } from "radix-ui";
import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;
export const PopoverAnchor = PopoverPrimitive.Anchor;
export const PopoverClose = PopoverPrimitive.Close;

export const floatingSurface =
  "z-50 rounded-md border border-border-strong bg-surface-2 text-text-1 shadow-popover outline-none data-[state=open]:animate-pop-in";

/** Popover content in a portal, with an optional arrow. Label it (`aria-label` or `aria-labelledby`). */
export function PopoverContent({
  className,
  sideOffset = 8,
  collisionPadding = 12,
  arrow = false,
  children,
  ...props
}: ComponentPropsWithRef<typeof PopoverPrimitive.Content> & { arrow?: boolean }) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        sideOffset={sideOffset}
        collisionPadding={collisionPadding}
        className={cn(floatingSurface, "w-72 p-4", className)}
        {...props}
      >
        {children}
        {arrow ? <PopoverPrimitive.Arrow width={12} height={6} className="fill-surface-2" /> : null}
      </PopoverPrimitive.Content>
    </PopoverPrimitive.Portal>
  );
}
