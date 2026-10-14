import { HoverCard as HoverCardPrimitive } from "radix-ui";
import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

import { floatingSurface } from "./popover";

/**
 * Hover-only previews for mouse users (e.g. a figure's provenance). Not
 * reachable by keyboard or touch: never put the only copy of information in
 * one. For explanations use InfoHint (hover AND click/Enter).
 */
export const HoverCard = HoverCardPrimitive.Root;
export const HoverCardTrigger = HoverCardPrimitive.Trigger;

export function HoverCardContent({
  className,
  sideOffset = 8,
  collisionPadding = 12,
  ...props
}: ComponentPropsWithRef<typeof HoverCardPrimitive.Content>) {
  return (
    <HoverCardPrimitive.Portal>
      <HoverCardPrimitive.Content
        sideOffset={sideOffset}
        collisionPadding={collisionPadding}
        className={cn(floatingSurface, "w-72 p-4 text-sm", className)}
        {...props}
      />
    </HoverCardPrimitive.Portal>
  );
}
