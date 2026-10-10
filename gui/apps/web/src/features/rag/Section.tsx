import type { ReactNode } from "react";

import { InfoHint } from "@/components/InfoHint";
import { cn } from "@/lib/cn";

/** A page section: an anchor, an h2 with an optional (i), a lead line, then content. */
export function Section({
  id,
  step,
  title,
  concept,
  lead,
  children,
  className,
}: {
  id: string;
  step: number;
  title: string;
  concept?: string;
  lead?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      className={cn("grid scroll-mt-20 grid-cols-[minmax(0,1fr)] gap-4", className)}
    >
      <div className="grid gap-1.5">
        <div className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="inline-flex size-6 items-center justify-center rounded-full border border-accent/50 font-mono text-xs text-accent-text"
          >
            {step}
          </span>
          <h2 id={`${id}-title`} className="text-xl font-semibold tracking-tight text-text-1">
            {title}
          </h2>
          {concept ? <InfoHint concept={concept} size="md" /> : null}
        </div>
        {lead ? <p className="max-w-3xl text-sm leading-relaxed text-text-2">{lead}</p> : null}
      </div>
      {children}
    </section>
  );
}
