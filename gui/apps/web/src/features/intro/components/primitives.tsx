/** Small building blocks shared by the INTRO sections. */
import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";

import { InfoHint } from "@/components/InfoHint";
import { cn } from "@/lib/cn";

/** Text whose `backticked` runs render as inline code (the quotes keep their markdown ticks). */
export function InlineCode({ text }: { text: string }) {
  const parts = text.split("`");
  return (
    <>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <code key={index} className="rounded-sm bg-surface-3 px-1 py-px font-mono text-[0.9em] text-text-1">
            {part}
          </code>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
}

/** A section of the page: an h2 with an optional (i), a lead paragraph, then content. */
export function IntroSection({
  id,
  eyebrow,
  title,
  concept,
  lead,
  actions,
  children,
  className,
}: {
  id: string;
  eyebrow?: string;
  title: string;
  concept?: string;
  lead?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const headingId = `${id}-title`;
  return (
    <section id={id} aria-labelledby={headingId} className={cn("grid scroll-mt-32 gap-5", className)}>
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div className="grid max-w-3xl gap-1.5">
          {eyebrow ? (
            <p className="font-mono text-[11px] font-semibold tracking-[0.16em] text-accent-text uppercase">
              {eyebrow}
            </p>
          ) : null}
          <div>
            <h2
              id={headingId}
              className="inline text-xl font-semibold tracking-tight text-balance text-text-1 md:text-2xl"
            >
              {title}
            </h2>
            {concept ? <InfoHint concept={concept} size="md" className="ml-1" /> : null}
          </div>
          {lead ? <div className="text-sm leading-relaxed text-text-2 md:text-[15px]">{lead}</div> : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

/** An outbound link (new tab) with the icon and the screen-reader note. */
export function ExternalAnchor({
  href,
  children,
  className,
  title,
}: {
  href: string;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={title}
      className={cn("inline-flex items-center gap-1 text-link hover:underline", className)}
    >
      {children}
      <ExternalLink className="size-3 shrink-0" aria-hidden="true" />
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}
