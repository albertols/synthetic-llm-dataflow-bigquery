import type { ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

/** Loading placeholder. Decorative: pair it with an aria-busy container or a visually hidden "Loading" label. */
export function Skeleton({ className, ...props }: ComponentPropsWithRef<"div">) {
  return <div aria-hidden="true" className={cn("animate-skeleton rounded-md bg-surface-3", className)} {...props} />;
}
