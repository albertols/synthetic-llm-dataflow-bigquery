import { Tabs as TabsPrimitive } from "radix-ui";
import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

/** In-page tabs (sections within a tab). Top-level navigation uses router links, not these. */
export const Tabs = TabsPrimitive.Root;

export function TabsList({ className, ...props }: ComponentPropsWithRef<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn(
        "inline-flex max-w-full items-center gap-1 overflow-x-auto rounded-md border border-border bg-surface-1 p-1",
        className,
      )}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: ComponentPropsWithRef<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "inline-flex h-8 cursor-pointer items-center justify-center gap-1.5 rounded-sm px-3 text-sm font-medium whitespace-nowrap text-text-2",
        "transition-colors hover:text-text-1 disabled:pointer-events-none disabled:opacity-50",
        "data-[state=active]:bg-surface-3 data-[state=active]:text-text-1 data-[state=active]:shadow-[inset_0_-2px_0_var(--accent)]",
        "[&_svg]:size-4",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({ className, ...props }: ComponentPropsWithRef<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content className={cn("mt-4 outline-none", className)} {...props} />;
}
