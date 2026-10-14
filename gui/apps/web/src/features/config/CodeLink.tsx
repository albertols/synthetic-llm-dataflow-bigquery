/**
 * A code citation at the commit knobs.json was exported from, with a 24 px
 * target (WCAG 2.5.8) so stacked citations stay easy to hit.
 */
import type { ReactNode } from "react";

import { SourceLink } from "@/components/SourceLink";
import { cn } from "@/lib/cn";

import { CODE_REF } from "./model/links";

export function CodeLink({
  source,
  children,
  className,
}: {
  source: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <SourceLink source={source} gitRef={CODE_REF} className={cn("min-h-6", className)}>
      {children}
    </SourceLink>
  );
}
