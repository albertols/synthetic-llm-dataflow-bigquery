import { Switch as SwitchPrimitive } from "radix-ui";
import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

/** On/off switch. Needs a label (`aria-label` or `<label htmlFor>`). The thumb position, not colour, shows state. */
export function Switch({ className, ...props }: ComponentPropsWithRef<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      className={cn(
        "peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border border-control-border bg-surface-3 transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-accent data-[state=checked]:bg-accent",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block size-3.5 translate-x-0.5 rounded-full bg-text-2 shadow transition-transform data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-accent-fg" />
    </SwitchPrimitive.Root>
  );
}
