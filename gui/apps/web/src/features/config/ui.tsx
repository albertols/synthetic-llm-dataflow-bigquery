/** Small building blocks shared by the CONFIG sections. */
import { ExternalLink } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { InfoHint } from "@/components/InfoHint";
import { cn } from "@/lib/cn";
import { formatCount } from "@/lib/format";

import { docUrl } from "./model/links";

/** A numbered part of a section (article 8's order), an h2 with its lead. */
export function Part({
  id,
  number,
  title,
  lead,
  children,
}: {
  id: string;
  number?: string;
  title: string;
  lead?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="grid gap-4">
      <div className="grid gap-1">
        <h2 id={id} className="flex items-baseline gap-2 text-lg font-semibold tracking-tight text-text-1">
          {number ? (
            <span className="font-mono text-sm text-accent-text" aria-hidden="true">
              {number}
            </span>
          ) : null}
          {title}
        </h2>
        {lead ? <p className="max-w-3xl text-sm text-text-2">{lead}</p> : null}
      </div>
      {children}
    </section>
  );
}

/** A synced design-doc figure (apps/web/public/assets, `npm run assets:sync`) with its claim and source doc. */
export function DocFigure({
  file,
  width,
  height,
  alt,
  claim,
  doc,
  className,
}: {
  file: string;
  width: number;
  height: number;
  alt: string;
  claim: string;
  doc: string;
  className?: string;
}) {
  return (
    <figure className={cn("m-0 grid gap-1.5", className)}>
      <a href={`/assets/${file}`} target="_blank" rel="noopener noreferrer" className="block rounded-md">
        <img
          src={`/assets/${file}`}
          width={width}
          height={height}
          alt={alt}
          loading="lazy"
          className="h-auto w-full rounded-md border border-border"
        />
        <span className="sr-only"> (full size, opens in a new tab)</span>
      </a>
      <figcaption className="text-xs text-text-3">
        {claim}{" "}
        <a
          href={docUrl(doc)}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-0.5 text-link underline"
        >
          {doc.split("/").pop()}
          <ExternalLink className="size-3" aria-hidden="true" />
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      </figcaption>
    </figure>
  );
}

/** A labelled output number with its (i). */
export function Output({
  label,
  value,
  concept,
  note,
  testId,
}: {
  label: string;
  value: ReactNode;
  concept?: string;
  note?: ReactNode;
  testId?: string;
}) {
  return (
    <div className="grid gap-0.5">
      <dt className="flex items-center gap-1 text-xs text-text-3">
        {label}
        {concept ? <InfoHint concept={concept} /> : null}
      </dt>
      <dd className="text-lg font-semibold text-text-1" data-testid={testId}>
        {value}
      </dd>
      {note ? <dd className="text-xs text-text-3">{note}</dd> : null}
    </div>
  );
}

/** "90M" → 90,000,000; accepts commas, underscores, k/M/B suffixes and exponents. */
export function parseCount(text: string): number | null {
  const clean = text.trim().replace(/[,_\s]/g, "");
  const match = /^(\d+(?:\.\d+)?(?:e\d+)?)([kKmMbB]?)$/.exec(clean);
  if (!match?.[1]) return null;
  const scale = { "": 1, k: 1e3, m: 1e6, b: 1e9 }[match[2]?.toLowerCase() ?? ""] ?? 1;
  const value = Math.round(Number(match[1]) * scale);
  return Number.isFinite(value) && value >= 1 ? value : null;
}

/**
 * A row-count field: type "90M", "1,000,000" or "1e6". Commits every valid
 * value as you type; while focused it keeps your text, on blur it shows the
 * committed number.
 */
export function CountField({
  label,
  value,
  onCommit,
  concept,
  max = 1e12,
  help,
}: {
  label: string;
  value: number;
  onCommit: (value: number) => void;
  concept?: string;
  max?: number;
  help?: ReactNode;
}) {
  const id = useId();
  const helpId = useId();
  const [draft, setDraft] = useState<string | null>(null);
  const parsed = draft === null ? value : parseCount(draft);
  const invalid = draft !== null && (parsed === null || parsed > max);
  return (
    <div className="grid content-start gap-1">
      <label htmlFor={id} className="flex items-center gap-1 text-xs font-medium text-text-2">
        {label}
        {concept ? <InfoHint concept={concept} /> : null}
      </label>
      <input
        id={id}
        inputMode="numeric"
        autoComplete="off"
        spellCheck={false}
        value={draft ?? formatCount(value)}
        aria-invalid={invalid || undefined}
        aria-describedby={helpId}
        onChange={(event) => {
          const text = event.target.value;
          setDraft(text);
          const next = parseCount(text);
          if (next !== null && next <= max && next !== value) onCommit(next);
        }}
        onBlur={() => setDraft(null)}
        className={cn(
          "h-9 w-full rounded-md border bg-surface-1 px-2.5 font-mono text-sm text-text-1 tabular-nums",
          invalid ? "border-status-critical" : "border-control-border",
        )}
      />
      <p id={helpId} className={cn("text-[11px]", invalid ? "text-status-critical-text" : "text-text-3")}>
        {invalid ? `Enter a whole number from 1 to ${formatCount(max)} (90M, 1,000,000 and 1e6 work).` : help}
      </p>
    </div>
  );
}
