import { Check, ChevronDown } from "lucide-react";
import { Select as SelectPrimitive } from "radix-ui";
import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

import { floatingSurface } from "./popover";

export const Select = SelectPrimitive.Root;
export const SelectGroup = SelectPrimitive.Group;
export const SelectValue = SelectPrimitive.Value;

/** Label the trigger: `aria-label`, or a `<label htmlFor>` pointing at its `id`. */
export function SelectTrigger({
  className,
  children,
  ...props
}: ComponentPropsWithRef<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger
      className={cn(
        "inline-flex h-9 w-full min-w-32 cursor-pointer items-center justify-between gap-2 rounded-md border border-control-border bg-surface-1 px-3 text-sm text-text-1",
        "hover:bg-surface-3 disabled:cursor-not-allowed disabled:opacity-50 data-[placeholder]:text-text-3 [&>span]:truncate",
        className,
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <ChevronDown className="size-4 text-text-3" aria-hidden="true" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

export function SelectContent({
  className,
  children,
  position = "popper",
  ...props
}: ComponentPropsWithRef<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        position={position}
        sideOffset={4}
        className={cn(
          floatingSurface,
          "max-h-(--radix-select-content-available-height) min-w-(--radix-select-trigger-width) overflow-hidden p-1",
          className,
        )}
        {...props}
      >
        <SelectPrimitive.Viewport>{children}</SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

export function SelectItem({ className, children, ...props }: ComponentPropsWithRef<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      className={cn(
        "relative flex cursor-pointer items-center rounded-sm py-1.5 pr-8 pl-2 text-sm text-text-1 outline-none select-none",
        "data-[disabled]:pointer-events-none data-[disabled]:opacity-50 data-[highlighted]:bg-surface-3",
        className,
      )}
      {...props}
    >
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
      <SelectPrimitive.ItemIndicator className="absolute right-2 inline-flex items-center">
        <Check className="size-4 text-accent-text" aria-hidden="true" />
      </SelectPrimitive.ItemIndicator>
    </SelectPrimitive.Item>
  );
}

export function SelectLabel({ className, ...props }: ComponentPropsWithRef<typeof SelectPrimitive.Label>) {
  return <SelectPrimitive.Label className={cn("px-2 py-1 text-xs font-medium text-text-3", className)} {...props} />;
}

export function SelectSeparator({ className, ...props }: ComponentPropsWithRef<typeof SelectPrimitive.Separator>) {
  return <SelectPrimitive.Separator className={cn("my-1 h-px bg-border", className)} {...props} />;
}
