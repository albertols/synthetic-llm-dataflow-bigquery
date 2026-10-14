import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

/** Card surface: surface-1 with a hairline border. Compose Header/Title/Description/Content/Footer. */
export function Card({ className, ...props }: ComponentPropsWithRef<"div">) {
  return (
    <div
      data-slot="card"
      className={cn("min-w-0 rounded-lg border border-border bg-surface-1 text-text-1 shadow-sm", className)}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: ComponentPropsWithRef<"div">) {
  return <div className={cn("flex flex-col gap-1 p-5 pb-3", className)} {...props} />;
}

export function CardTitle({ className, children, ...props }: ComponentPropsWithRef<"h3">) {
  return (
    <h3 className={cn("text-base leading-tight font-semibold tracking-tight text-text-1", className)} {...props}>
      {children}
    </h3>
  );
}

export function CardDescription({ className, ...props }: ComponentPropsWithRef<"p">) {
  return <p className={cn("text-sm text-text-2", className)} {...props} />;
}

export function CardContent({ className, ...props }: ComponentPropsWithRef<"div">) {
  return <div className={cn("p-5 pt-0", className)} {...props} />;
}

export function CardFooter({ className, ...props }: ComponentPropsWithRef<"div">) {
  return <div className={cn("flex items-center gap-2 border-t border-border px-5 py-3", className)} {...props} />;
}
