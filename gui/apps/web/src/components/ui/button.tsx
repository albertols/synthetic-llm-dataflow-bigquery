import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

export const buttonVariants = cva(
  [
    "inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 rounded-md font-medium whitespace-nowrap select-none",
    "transition-colors duration-(--duration-fast) disabled:pointer-events-none disabled:opacity-50",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring",
    "[&_svg]:pointer-events-none [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        primary: "bg-accent text-accent-fg hover:bg-accent-hover",
        secondary:
          "border border-border-strong bg-surface-3 text-text-1 hover:bg-surface-2 hover:border-control-border",
        outline: "border border-control-border bg-transparent text-text-1 hover:bg-surface-3",
        ghost: "bg-transparent text-text-2 hover:bg-surface-3 hover:text-text-1",
        danger: "bg-status-critical text-white hover:brightness-110",
        link: "h-auto px-0 text-link underline-offset-4 hover:underline",
      },
      size: {
        sm: "h-8 px-3 text-xs [&_svg]:size-3.5",
        md: "h-9 px-4 text-sm [&_svg]:size-4",
        lg: "h-11 px-5 text-base [&_svg]:size-5",
        icon: "size-9 [&_svg]:size-4",
        "icon-sm": "size-8 [&_svg]:size-4",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export type ButtonProps = ComponentPropsWithRef<"button"> &
  VariantProps<typeof buttonVariants> & {
    /** Render the child element (e.g. a router Link) with button styling. */
    asChild?: boolean;
  };

/** Buttons: primary (Beam orange, one per view), secondary, outline, ghost, danger, link. Icon-only buttons need `aria-label`. */
export function Button({ className, variant, size, asChild = false, type, ...props }: ButtonProps) {
  const Comp = asChild ? Slot.Root : "button";
  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size }), className)}
      {...(asChild ? {} : { type: type ?? "button" })}
      {...props}
    />
  );
}
