import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

export const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap [&_svg]:size-3 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        neutral: "border-border-strong bg-surface-3 text-text-2",
        accent: "border-accent/40 bg-accent-soft text-accent-text",
        info: "border-brand-blue/45 bg-info-soft text-link",
        cpu: "border-cpu/45 bg-cpu/12 text-cpu-text",
        gpu: "border-gpu/55 bg-gpu/15 text-gpu-text",
        outline: "border-control-border bg-transparent text-text-2",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export type BadgeProps = ComponentPropsWithRef<"span"> & VariantProps<typeof badgeVariants>;

/** Small label. Colour is never the only signal: badges always carry text. Status uses StatusPill instead. */
export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}
