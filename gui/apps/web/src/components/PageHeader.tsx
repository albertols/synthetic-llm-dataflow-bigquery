import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

import { InfoHint } from "./InfoHint";

export type PageHeaderProps = {
  /** Small label above the title (the tab name). */
  eyebrow?: string;
  /** The page's one h1. */
  title: string;
  description?: ReactNode;
  concept?: string;
  actions?: ReactNode;
  className?: string;
};

/** Page title block every tab starts with: eyebrow, h1, lead paragraph, actions. */
export function PageHeader({ eyebrow, title, description, concept, actions, className }: PageHeaderProps) {
  return (
    <header className={cn("flex flex-col gap-4 md:flex-row md:items-end md:justify-between", className)}>
      <div className="flex min-w-0 flex-col gap-2">
        {eyebrow ? (
          <p className="font-mono text-xs font-semibold tracking-[0.18em] text-accent-text uppercase">{eyebrow}</p>
        ) : null}
        <div className="flex items-center gap-1.5">
          <h1 className="text-2xl font-semibold tracking-tight text-balance text-text-1 md:text-3xl">{title}</h1>
          {concept ? <InfoHint concept={concept} size="md" side="bottom" /> : null}
        </div>
        {description ? (
          <div className="max-w-3xl text-sm leading-relaxed text-text-2 md:text-base">{description}</div>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
