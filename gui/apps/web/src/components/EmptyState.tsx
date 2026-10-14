import { Inbox, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type EmptyStateProps = {
  title: string;
  description?: ReactNode;
  icon?: LucideIcon;
  /** A call to action (Button, Link). */
  action?: ReactNode;
  /** Compact variant for inside a chart frame or a table. */
  compact?: boolean;
  /** Heading level for the title (default 2; 3 inside a titled card; "none" inside a figure, where it is a message, not a section). */
  headingLevel?: 2 | 3 | 4 | "none";
  className?: string;
  children?: ReactNode;
};

/** A labelled empty state: what is missing and, when possible, why and what to do next. */
export function EmptyState({
  title,
  description,
  icon: Icon = Inbox,
  action,
  compact = false,
  headingLevel = 2,
  className,
  children,
}: EmptyStateProps) {
  const Heading = headingLevel === "none" ? "p" : (`h${headingLevel}` as const);
  return (
    <div
      data-slot="empty-state"
      className={cn(
        "flex flex-col items-center justify-center text-center",
        compact
          ? "gap-2 px-4 py-6"
          : "gap-3 rounded-lg border border-dashed border-border-strong bg-surface-1/60 px-6 py-12",
        className,
      )}
    >
      <span
        className={cn(
          "inline-flex items-center justify-center rounded-full bg-surface-3 text-text-3",
          compact ? "size-9 [&_svg]:size-4" : "size-12 [&_svg]:size-6",
        )}
      >
        <Icon aria-hidden="true" />
      </span>
      <Heading className={cn("font-semibold text-text-1", compact ? "text-sm" : "text-base")}>{title}</Heading>
      {description ? (
        <div className={cn("max-w-prose text-text-2", compact ? "text-xs" : "text-sm")}>{description}</div>
      ) : null}
      {children}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}
