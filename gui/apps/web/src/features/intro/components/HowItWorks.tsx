/**
 * "How it works": one card per DESIGN.md section, each with its Claim quoted
 * verbatim, a figure copied by assets:sync (provenance on hover, and in the
 * enlarged view for keyboard and touch), the ADRs the section summarizes, and
 * links to the section on GitHub and to the tab that explores it.
 */
import { ArrowRight, CircleDashed, Maximize2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import { InfoHint } from "@/components/InfoHint";
import { Mermaid } from "@/components/Mermaid";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";
import { formatBytes } from "@/lib/format";
import { repoBlobUrl } from "@/lib/links";
import { useInViewOnce } from "@/lib/useInViewOnce";

import { ADRS, adrUrl, adrsForSection, findAdr, plainTitle } from "../content/adrs";
import {
  DESIGN_SECTIONS,
  designUrl,
  quoteSourceUrl,
  type DesignSection,
  type ImageVisual,
} from "../content/designSections";
import { assetUrl, useProvenance, type ProvenanceEntry } from "../content/provenance";
import { TargetLink } from "../content/targets";
import { ExternalAnchor, InlineCode, IntroSection } from "./primitives";

export function HowItWorks() {
  return (
    <IntroSection
      id="how-it-works"
      eyebrow="How it works"
      title="The design, one claim per section"
      concept="intro:claim"
      lead={
        <>
          Each card quotes its section of <ExternalAnchor href={designUrl()}>docs/DESIGN.md</ExternalAnchor> word for
          word, with the figure that backs it. Hover a figure for its provenance, or open it for the full view.
        </>
      }
    >
      <ol className="grid gap-4 md:grid-cols-2 xl:grid-cols-3" aria-label="DESIGN.md sections">
        {DESIGN_SECTIONS.map((section) => (
          <li key={section.number} className="min-w-0">
            <DesignCard section={section} />
          </li>
        ))}
      </ol>
    </IntroSection>
  );
}

function DesignCard({ section }: { section: DesignSection }) {
  const headingId = `design-s${section.number}`;
  const adrs = adrsForSection(section.number);
  return (
    <article
      aria-labelledby={headingId}
      data-section={section.number}
      className="flex h-full flex-col overflow-hidden rounded-lg border border-border bg-surface-1"
    >
      <SectionVisualSlot section={section} />
      <div className="flex flex-1 flex-col gap-3 p-5">
        <span className="font-mono text-xs font-semibold text-accent-text">§{section.number}</span>
        <h3 id={headingId} className="-mt-1 text-base leading-snug font-semibold text-text-1">
          {section.title}
        </h3>
        <blockquote className="grid gap-1.5">
          <p className="font-mono text-[10.5px] font-semibold tracking-[0.14em] text-text-3 uppercase">
            {section.quote.kind === "claim"
              ? "Claim"
              : section.quote.source === "DESIGN.md"
                ? "Lead (no claim line)"
                : "From the package README"}
          </p>
          <p className="text-[15px] leading-relaxed text-text-1">
            <InlineCode text={section.quote.text} />
          </p>
        </blockquote>
        {adrs.length ? <AdrChips numbers={adrs.map((a) => a.number)} /> : null}
        <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border pt-3 text-sm">
          <ExternalAnchor href={quoteSourceUrl(section.quote, section)}>
            {section.quote.source === "DESIGN.md"
              ? `Read §${section.number} in DESIGN.md`
              : `Read ${section.quote.source}`}
          </ExternalAnchor>
          {section.explore ? (
            <TargetLink
              to={section.explore.target}
              className="inline-flex items-center gap-1 text-text-2 hover:text-text-1 hover:underline"
            >
              {section.explore.label}
              <ArrowRight className="size-3.5" aria-hidden="true" />
            </TargetLink>
          ) : null}
        </div>
      </div>
    </article>
  );
}

/** ADR numbers as small links (title in the accessible name); a number DESIGN.md does not map yet is pending. */
export function AdrChips({ numbers, className }: { numbers: readonly string[]; className?: string }) {
  return (
    <ul aria-label="Decision records" className={cn("flex flex-wrap gap-1", className)}>
      {numbers.map((number) => {
        const adr = findAdr(number);
        return (
          <li key={number}>
            {adr ? (
              <a
                href={adrUrl(adr)}
                target="_blank"
                rel="noopener noreferrer"
                title={`ADR ${number}: ${plainTitle(adr.title)}`}
                className="inline-flex h-6 items-center rounded-sm border border-border-strong bg-surface-2 px-1.5 font-mono text-[11px] text-text-2 hover:border-control-border hover:text-text-1"
              >
                <span className="sr-only">ADR </span>
                {number}
                <span className="sr-only">: {plainTitle(adr.title)} (opens in a new tab)</span>
              </a>
            ) : (
              <span
                title={`ADR ${number} is not in DESIGN.md's map on this branch yet`}
                className="inline-flex h-6 items-center gap-1 rounded-sm border border-dashed border-border-strong px-1.5 font-mono text-[11px] text-text-3"
              >
                <CircleDashed className="size-3" aria-hidden="true" />
                <span className="sr-only">ADR </span>
                {number}
                <span className="sr-only">, pending</span>
                <span aria-hidden="true">· pending</span>
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function SectionVisualSlot({ section }: { section: DesignSection }) {
  const visual = section.visual;
  switch (visual.kind) {
    case "image":
      return <FigureButton visual={visual} section={section} />;
    case "mermaid":
      return <MermaidSlot chart={visual.chart} ariaLabel={visual.ariaLabel} />;
    case "adr-map":
      return <AdrMapVisual />;
    case "figure-mosaic":
      return <FigureMosaic />;
  }
}

const SLOT = "aspect-[16/10] w-full min-h-0 overflow-hidden border-b border-border";

/** A figure on its white "paper" (the committed PNGs are drawn on white). */
function FigureButton({ visual, section }: { visual: ImageVisual; section: DesignSection }) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState(false);
  const provenance = useProvenance();
  const entry = provenance.data?.get(visual.file);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        setPreview(false);
      }}
    >
      {/* Hover (or focus) previews the provenance; the dialog repeats it for keyboard and touch. */}
      <HoverCard open={preview && !open} onOpenChange={setPreview} openDelay={350} closeDelay={120}>
        <HoverCardTrigger asChild>
          <DialogTrigger asChild>
            <button
              type="button"
              className={cn(SLOT, "group relative block cursor-zoom-in bg-white outline-offset-[-3px]")}
            >
              <img
                src={assetUrl(visual.file)}
                alt={visual.alt}
                loading="lazy"
                decoding="async"
                className="absolute inset-2 size-[calc(100%-1rem)] object-contain"
              />
              <span className="sr-only">. Open the figure with its provenance.</span>
              <span
                aria-hidden="true"
                className="absolute right-2 bottom-2 inline-flex items-center gap-1 rounded-sm bg-bg/85 px-1.5 py-0.5 text-[11px] font-medium text-text-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
              >
                <Maximize2 className="size-3" />
                Enlarge
              </span>
            </button>
          </DialogTrigger>
        </HoverCardTrigger>
        <HoverCardContent side="top" className="w-80">
          <ProvenanceDetails file={visual.file} entry={entry} loading={provenance.isPending} compact />
        </HoverCardContent>
      </HoverCard>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle>
            §{section.number} · {section.title}
          </DialogTitle>
          <DialogDescription>{visual.alt}</DialogDescription>
        </DialogHeader>
        <figure className="grid gap-3">
          <div className="rounded-md bg-white p-3">
            <img
              src={assetUrl(visual.file)}
              alt={visual.alt}
              className="mx-auto max-h-[60dvh] w-auto max-w-full object-contain"
            />
          </div>
          {visual.caption ? (
            <figcaption className="text-sm text-text-2 italic">
              <InlineCode text={visual.caption} />
            </figcaption>
          ) : null}
        </figure>
        {[section.quote, ...(section.more ?? [])].map((quote) => (
          <blockquote key={quote.text} className="border-l-2 border-accent pl-3 text-sm text-text-1">
            <span className="mr-1 font-mono text-[10.5px] font-semibold tracking-[0.14em] text-text-3 uppercase">
              {quote.kind === "claim" ? "Claim" : "Lead"}
            </span>
            <InlineCode text={quote.text} />
          </blockquote>
        ))}
        <div className="rounded-md border border-border bg-surface-1 p-3">
          <ProvenanceDetails file={visual.file} entry={entry} loading={provenance.isPending} />
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ProvenanceDetails({
  file,
  entry,
  loading,
  compact = false,
}: {
  file: string;
  entry: ProvenanceEntry | undefined;
  loading: boolean;
  compact?: boolean;
}) {
  if (!entry) {
    return (
      <p className="text-sm text-text-2">
        {loading ? "Loading the provenance…" : `No provenance entry for ${file}: re-run npm run assets:sync.`}
      </p>
    );
  }
  const rows: Array<{ label: string; value: ReactNode }> = [
    {
      label: "Source",
      value: <ExternalAnchor href={repoBlobUrl(entry.source)}>{entry.source}</ExternalAnchor>,
    },
    {
      label: "Generated by",
      value: entry.generator ? (
        <ExternalAnchor href={repoBlobUrl(entry.generator)}>{entry.generator}</ExternalAnchor>
      ) : (
        "no generating script in the repository"
      ),
    },
    {
      label: "Documented in",
      value: entry.documentedIn ? (
        <ExternalAnchor href={repoBlobUrl(entry.documentedIn)}>{entry.documentedIn}</ExternalAnchor>
      ) : (
        "—"
      ),
    },
    { label: "SHA-256", value: <code className="font-mono">{entry.sha256.slice(0, 16)}…</code> },
    { label: "Size", value: formatBytes(entry.bytes) },
  ];
  return (
    <div className="grid gap-2">
      <p className="text-xs font-semibold tracking-wide text-text-1">
        Provenance <span className="font-mono font-normal text-text-3">{entry.file}</span>
      </p>
      <dl className={cn("grid grid-cols-[auto_1fr] gap-x-3 gap-y-1", compact ? "text-xs" : "text-sm")}>
        {rows.map((row) => (
          <div key={row.label} className="contents">
            <dt className="text-text-3">{row.label}</dt>
            <dd className="min-w-0 [overflow-wrap:anywhere] text-text-2">{row.value}</dd>
          </div>
        ))}
      </dl>
      {entry.attribution ? <p className="text-xs text-text-3">{entry.attribution}</p> : null}
    </div>
  );
}

function MermaidSlot({ chart, ariaLabel }: { chart: string; ariaLabel: string }) {
  const [ref, seen] = useInViewOnce<HTMLDivElement>();
  return (
    <div
      ref={ref}
      className="flex min-h-48 w-full items-center justify-center border-b border-border bg-surface-2 p-2 md:aspect-[16/10] md:overflow-hidden"
    >
      {seen ? (
        <Mermaid
          chart={chart}
          narrowDirection="TB"
          ariaLabel={ariaLabel}
          className="w-full border-0 bg-transparent p-0"
        />
      ) : (
        <Skeleton className="h-44 w-full" />
      )}
    </div>
  );
}

/** §9: how many decision records each section summarizes. */
function AdrMapVisual() {
  const sections = DESIGN_SECTIONS.map((s) => ({ ...s, adrs: adrsForSection(s.number) })).filter(
    (s) => s.adrs.length > 0,
  );
  const most = Math.max(...sections.map((s) => s.adrs.length));
  return (
    <div className={cn(SLOT, "flex flex-col justify-center gap-1.5 bg-surface-2 px-4 py-3")}>
      <p className="flex items-center gap-1 text-xs text-text-2">
        {ADRS.length} decision records, mapped to {sections.length} sections
        <InfoHint concept="intro:adr" />
      </p>
      <ul className="grid gap-1" aria-label="Decision records per section">
        {sections.map((s) => (
          <li key={s.number} className="grid grid-cols-[2.25rem_1fr_1.5rem] items-center gap-2 text-[11px]">
            <span className="font-mono text-text-3">§{s.number}</span>
            <span
              aria-hidden="true"
              className="h-2 rounded-r-sm bg-chart-1"
              style={{ width: `${(s.adrs.length / most) * 100}%` }}
            />
            <span className="text-right font-mono text-text-2">
              {s.adrs.length}
              <span className="sr-only"> ADRs: {s.title}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** §10: the figures DESIGN.md itself carries, as the provenance file lists them. */
function FigureMosaic() {
  const provenance = useProvenance();
  const figures = [...(provenance.data?.values() ?? [])].filter((e) => e.documentedIn === "docs/DESIGN.md");
  return (
    <div className={cn(SLOT, "grid content-center gap-2 bg-surface-2 p-3")}>
      {provenance.isPending ? (
        <Skeleton className="size-full" />
      ) : (
        <>
          <ul className="grid grid-cols-4 gap-1.5" aria-label="Figures DESIGN.md carries">
            {figures.map((figure) => (
              <li key={figure.file} className="aspect-[4/3] overflow-hidden rounded-sm bg-white p-0.5">
                <img
                  src={assetUrl(figure.file)}
                  alt={`${figure.file}, generated by ${figure.generator ?? "no script"}`}
                  title={`${figure.file} — ${figure.generator ?? "no generating script"}`}
                  loading="lazy"
                  decoding="async"
                  className="size-full object-contain"
                />
              </li>
            ))}
          </ul>
          <p className="flex items-center gap-1 text-[11px] text-text-3">
            {figures.length} figures in DESIGN.md, each with its generating script and SHA-256.
            <InfoHint concept="intro:figure-provenance" />
          </p>
        </>
      )}
    </div>
  );
}
