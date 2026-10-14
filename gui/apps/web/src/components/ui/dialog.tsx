import { X } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;
export const DialogPortal = DialogPrimitive.Portal;

export function DialogOverlay({ className, ...props }: ComponentPropsWithRef<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      className={cn("fixed inset-0 z-50 bg-overlay backdrop-blur-[2px] data-[state=open]:animate-fade-in", className)}
      {...props}
    />
  );
}

/** Modal dialog. Always render a DialogTitle (use VisuallyHidden if it must not show). */
export function DialogContent({
  className,
  children,
  hideClose = false,
  ...props
}: ComponentPropsWithRef<typeof DialogPrimitive.Content> & { hideClose?: boolean }) {
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Content
        className={cn(
          "fixed top-1/2 left-1/2 z-50 grid max-h-[85dvh] w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 gap-4 overflow-y-auto",
          "rounded-lg border border-border-strong bg-surface-2 p-6 text-text-1 shadow-popover outline-none data-[state=open]:animate-pop-in",
          className,
        )}
        {...props}
      >
        {children}
        {hideClose ? null : (
          <DialogPrimitive.Close
            aria-label="Close"
            className="absolute top-3 right-3 inline-flex size-8 items-center justify-center rounded-md text-text-3 hover:bg-surface-3 hover:text-text-1"
          >
            <X className="size-4" aria-hidden="true" />
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPortal>
  );
}

export function DialogHeader({ className, ...props }: ComponentPropsWithRef<"div">) {
  return <div className={cn("flex flex-col gap-1.5 pr-8", className)} {...props} />;
}

export function DialogFooter({ className, ...props }: ComponentPropsWithRef<"div">) {
  return <div className={cn("flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", className)} {...props} />;
}

export function DialogTitle({ className, ...props }: ComponentPropsWithRef<typeof DialogPrimitive.Title>) {
  return <DialogPrimitive.Title className={cn("text-lg font-semibold tracking-tight", className)} {...props} />;
}

export function DialogDescription({ className, ...props }: ComponentPropsWithRef<typeof DialogPrimitive.Description>) {
  return <DialogPrimitive.Description className={cn("text-sm text-text-2", className)} {...props} />;
}
