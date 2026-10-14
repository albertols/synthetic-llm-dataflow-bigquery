import { Check } from "lucide-react";
import { DropdownMenu as MenuPrimitive } from "radix-ui";
import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

import { floatingSurface } from "./popover";

/** Menus of actions (overflow menus, row actions). Arrow keys move, Enter picks, Esc closes. */
export const DropdownMenu = MenuPrimitive.Root;
export const DropdownMenuTrigger = MenuPrimitive.Trigger;
export const DropdownMenuGroup = MenuPrimitive.Group;
/** A labelled set of exclusive choices (`role="group"`); give it `aria-labelledby` or `aria-label`. */
export const DropdownMenuRadioGroup = MenuPrimitive.RadioGroup;

export function DropdownMenuContent({
  className,
  sideOffset = 6,
  align = "end",
  ...props
}: ComponentPropsWithRef<typeof MenuPrimitive.Content>) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content
        sideOffset={sideOffset}
        align={align}
        collisionPadding={8}
        className={cn(floatingSurface, "min-w-52 p-1", className)}
        {...props}
      />
    </MenuPrimitive.Portal>
  );
}

export function DropdownMenuItem({ className, ...props }: ComponentPropsWithRef<typeof MenuPrimitive.Item>) {
  return (
    <MenuPrimitive.Item
      className={cn(
        "flex cursor-pointer items-center gap-2 rounded-sm px-2 py-2 text-sm text-text-1 outline-none select-none",
        "data-[disabled]:pointer-events-none data-[disabled]:opacity-50 data-[highlighted]:bg-surface-3 [&_svg]:size-4 [&_svg]:text-text-3",
        className,
      )}
      {...props}
    />
  );
}

/** One choice of a radio group: `role="menuitemradio"` with `aria-checked`, a tick when chosen. */
export function DropdownMenuRadioItem({
  className,
  children,
  ...props
}: ComponentPropsWithRef<typeof MenuPrimitive.RadioItem>) {
  return (
    <MenuPrimitive.RadioItem
      className={cn(
        "relative flex cursor-pointer items-center gap-2 rounded-sm py-2 pr-2 pl-8 text-sm text-text-1 outline-none select-none",
        "data-[disabled]:pointer-events-none data-[disabled]:opacity-50 data-[highlighted]:bg-surface-3 [&_svg]:size-4 [&_svg]:text-text-3",
        className,
      )}
      {...props}
    >
      <span className="absolute left-2 inline-flex size-4 items-center justify-center">
        <MenuPrimitive.ItemIndicator>
          <Check className="text-accent-text!" aria-hidden="true" />
        </MenuPrimitive.ItemIndicator>
      </span>
      {children}
    </MenuPrimitive.RadioItem>
  );
}

export function DropdownMenuLabel({ className, ...props }: ComponentPropsWithRef<typeof MenuPrimitive.Label>) {
  return <MenuPrimitive.Label className={cn("px-2 py-1 text-xs font-medium text-text-3", className)} {...props} />;
}

export function DropdownMenuSeparator({ className, ...props }: ComponentPropsWithRef<typeof MenuPrimitive.Separator>) {
  return <MenuPrimitive.Separator className={cn("my-1 h-px bg-border", className)} {...props} />;
}
