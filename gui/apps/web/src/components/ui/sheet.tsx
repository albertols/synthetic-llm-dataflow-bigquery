import { cva, type VariantProps } from "class-variance-authority";
import { X } from "lucide-react";
import { Dialog as SheetPrimitive } from "radix-ui";
import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

import { DialogOverlay } from "./dialog";

export const Sheet = SheetPrimitive.Root;
export const SheetTrigger = SheetPrimitive.Trigger;
export const SheetClose = SheetPrimitive.Close;

const sheetVariants = cva(
  "fixed z-50 flex flex-col gap-4 overflow-y-auto border-border-strong bg-surface-2 p-6 text-text-1 shadow-popover outline-none",
  {
    variants: {
      side: {
        right: "inset-y-0 right-0 h-full w-full border-l sm:max-w-md data-[state=open]:animate-slide-in-right",
        left: "inset-y-0 left-0 h-full w-full border-r sm:max-w-md data-[state=open]:animate-fade-in",
        bottom: "inset-x-0 bottom-0 max-h-[85dvh] border-t rounded-t-lg data-[state=open]:animate-fade-in",
      },
    },
    defaultVariants: { side: "right" },
  },
);

/** Side panel (drawers: column details, knob sheets). A Radix Dialog: focus trapped, Esc closes. Needs a SheetTitle. */
export function SheetContent({
  className,
  side,
  children,
  ...props
}: ComponentPropsWithRef<typeof SheetPrimitive.Content> & VariantProps<typeof sheetVariants>) {
  return (
    <SheetPrimitive.Portal>
      <DialogOverlay />
      <SheetPrimitive.Content className={cn(sheetVariants({ side }), className)} {...props}>
        {children}
        <SheetPrimitive.Close
          aria-label="Close"
          className="absolute top-3 right-3 inline-flex size-8 items-center justify-center rounded-md text-text-3 hover:bg-surface-3 hover:text-text-1"
        >
          <X className="size-4" aria-hidden="true" />
        </SheetPrimitive.Close>
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  );
}

export function SheetHeader({ className, ...props }: ComponentPropsWithRef<"div">) {
  return <div className={cn("flex flex-col gap-1.5 pr-8", className)} {...props} />;
}

export function SheetTitle({ className, ...props }: ComponentPropsWithRef<typeof SheetPrimitive.Title>) {
  return <SheetPrimitive.Title className={cn("text-lg font-semibold tracking-tight", className)} {...props} />;
}

export function SheetDescription({ className, ...props }: ComponentPropsWithRef<typeof SheetPrimitive.Description>) {
  return <SheetPrimitive.Description className={cn("text-sm text-text-2", className)} {...props} />;
}
