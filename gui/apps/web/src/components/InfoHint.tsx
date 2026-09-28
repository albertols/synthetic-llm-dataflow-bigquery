/**
 * InfoHint — the (i) next to every non-trivial concept.
 *
 *   <InfoHint concept="metric:column.ks" />            // top, small
 *   <InfoHint concept="knob:reference_rows_limit" side="right" size="md" />
 *
 * A Radix Popover (role="dialog", labelled by the concept title):
 * - opens on hover after 150 ms (mouse and pen; touch uses tap), and stays
 *   open while the pointer is over the trigger or the popover;
 * - opens on click, Enter or Space and then stays open ("pinned") until
 *   Esc, a click outside, or another click on the trigger; focus moves into
 *   the popover and returns to the trigger on close;
 * - shows title, level, purpose, formula (KaTeX, lazy), mini diagram,
 *   interpretation, pitfalls and links (new tab, rel="noopener noreferrer").
 * An unknown concept id (after every concept file has loaded) logs
 * `console.error` in development and renders a neutral, non-interactive
 * icon — add the concept to `gui/packages/contracts/src/concepts/<owner>.ts`.
 */
import {
  BookOpen,
  CircleCheck,
  CircleHelp,
  ExternalLink,
  FileCode2,
  Info,
  Landmark,
  Lightbulb,
  ScrollText,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import { Popover } from "radix-ui";
import { useEffect, useId, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { cn } from "@/lib/cn";
import { useConcept, type Concept, type ConceptLinkKind } from "@/lib/concepts";
import { LEVELS, levelChipClass } from "@/lib/levels";

import { Formula } from "./Formula";
import { MiniDiagram } from "./MiniDiagram";

export type InfoHintProps = {
  /** Concept id from the registry, e.g. "metric:column.ks". */
  concept: string;
  side?: "top" | "right" | "bottom" | "left";
  size?: "sm" | "md";
  className?: string;
};

const OPEN_DELAY_MS = 150;
const CLOSE_DELAY_MS = 150;

const TRIGGER_SIZE = { sm: "size-6 [&_svg]:size-3.5", md: "size-7 [&_svg]:size-4" } as const;

export function InfoHint({ concept: id, side = "top", size = "sm", className }: InfoHintProps) {
  const lookup = useConcept(id);
  if (lookup.status === "ready") {
    return <ConceptHint concept={lookup.concept} side={side} size={size} className={className} />;
  }
  if (lookup.status === "loading") {
    // Tab concept files arrive in one lazy batch (prefetched at idle); hold the space meanwhile.
    return (
      <span
        aria-hidden="true"
        className={cn(
          "inline-flex shrink-0 items-center justify-center text-text-3 opacity-40",
          TRIGGER_SIZE[size],
          className,
        )}
      >
        <Info />
      </span>
    );
  }
  return <UnknownHint id={id} size={size} className={className} />;
}

function UnknownHint({ id, size, className }: { id: string; size: "sm" | "md"; className?: string }) {
  useEffect(() => {
    if (import.meta.env.DEV) {
      console.error(`[InfoHint] unknown concept id "${id}" — add it to gui/packages/contracts/src/concepts/<owner>.ts`);
    }
  }, [id]);
  return (
    <span
      role="img"
      aria-label="No explanation available"
      className={cn(
        "inline-flex shrink-0 items-center justify-center text-text-3 opacity-60",
        TRIGGER_SIZE[size],
        className,
      )}
    >
      <CircleHelp aria-hidden="true" />
    </span>
  );
}

type OpenedBy = "hover" | "pin";

function ConceptHint({
  concept,
  side,
  size,
  className,
}: {
  concept: Concept;
  side: NonNullable<InfoHintProps["side"]>;
  size: "sm" | "md";
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const openedBy = useRef<OpenedBy>("hover");
  const openTimer = useRef<number | undefined>(undefined);
  const closeTimer = useRef<number | undefined>(undefined);
  const contentRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  const clearTimers = () => {
    window.clearTimeout(openTimer.current);
    window.clearTimeout(closeTimer.current);
  };
  useEffect(() => clearTimers, []);

  const onPointerEnter = (event: ReactPointerEvent) => {
    if (event.pointerType === "touch") return;
    window.clearTimeout(closeTimer.current);
    if (open) return;
    openTimer.current = window.setTimeout(() => {
      openedBy.current = "hover";
      setOpen(true);
    }, OPEN_DELAY_MS);
  };

  const onPointerLeave = (event: ReactPointerEvent) => {
    if (event.pointerType === "touch") return;
    window.clearTimeout(openTimer.current);
    if (!open || openedBy.current === "pin") return;
    closeTimer.current = window.setTimeout(() => setOpen(false), CLOSE_DELAY_MS);
  };

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        clearTimers();
        setOpen(next);
      }}
    >
      <Popover.Trigger
        aria-label={`About: ${concept.title}`}
        data-concept={concept.id}
        onPointerEnter={onPointerEnter}
        onPointerLeave={onPointerLeave}
        onClick={(event) => {
          clearTimers();
          if (open && openedBy.current === "hover") {
            // A click on a hover-opened hint pins it instead of closing it.
            event.preventDefault();
          }
          openedBy.current = "pin";
        }}
        className={cn(
          "inline-flex shrink-0 cursor-pointer items-center justify-center rounded-full align-middle text-text-3",
          "transition-colors hover:bg-surface-3 hover:text-text-1 data-[state=open]:bg-surface-3 data-[state=open]:text-accent-text",
          TRIGGER_SIZE[size],
          className,
        )}
      >
        <Info aria-hidden="true" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          ref={contentRef}
          tabIndex={-1}
          side={side}
          sideOffset={6}
          collisionPadding={12}
          aria-labelledby={titleId}
          onPointerEnter={() => window.clearTimeout(closeTimer.current)}
          onPointerLeave={onPointerLeave}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            if (openedBy.current === "pin") contentRef.current?.focus({ preventScroll: true });
          }}
          onCloseAutoFocus={(event) => {
            if (openedBy.current === "hover") event.preventDefault();
            openedBy.current = "hover";
          }}
          className={cn(
            "z-50 grid max-h-[min(34rem,var(--radix-popover-content-available-height))] w-[min(23rem,calc(100vw-2rem))] gap-3 overflow-y-auto",
            "rounded-md border border-border-strong bg-surface-2 p-4 text-left text-text-1 shadow-popover outline-none data-[state=open]:animate-pop-in",
          )}
        >
          <ConceptBody concept={concept} titleId={titleId} />
          <Popover.Arrow width={12} height={6} className="fill-surface-2" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

const LINK_KIND: Record<ConceptLinkKind, { label: string; icon: LucideIcon }> = {
  paper: { label: "Paper", icon: ScrollText },
  docs: { label: "Docs", icon: BookOpen },
  adr: { label: "ADR", icon: Landmark },
  code: { label: "Code", icon: FileCode2 },
};

function LevelTag({ level }: { level: NonNullable<Concept["level"]> }) {
  const { label, icon: Icon } = LEVELS[level];
  return (
    <span className={levelChipClass("sm")}>
      <Icon aria-hidden="true" />
      {label}
    </span>
  );
}

function ConceptBody({ concept, titleId }: { concept: Concept; titleId: string }) {
  const { interpretation } = concept;
  const rows: Array<{ key: string; label: string; icon: LucideIcon; tone: string; text: string }> = [];
  if (interpretation?.good)
    rows.push({
      key: "good",
      label: "Good",
      icon: CircleCheck,
      tone: "text-status-good-text",
      text: interpretation.good,
    });
  if (interpretation?.bad)
    rows.push({
      key: "bad",
      label: "Watch",
      icon: TriangleAlert,
      tone: "text-status-serious-text",
      text: interpretation.bad,
    });
  if (interpretation?.tip)
    rows.push({ key: "tip", label: "Tip", icon: Lightbulb, tone: "text-link", text: interpretation.tip });
  if (concept.pitfalls)
    rows.push({
      key: "pitfall",
      label: "Pitfall",
      icon: TriangleAlert,
      tone: "text-status-warn-text",
      text: concept.pitfalls,
    });

  return (
    <>
      <div className="flex items-start justify-between gap-3">
        <h2 id={titleId} className="text-sm leading-snug font-semibold text-text-1">
          {concept.title}
        </h2>
        {concept.level ? <LevelTag level={concept.level} /> : null}
      </div>
      <p className="text-sm leading-relaxed text-text-2">{concept.purpose}</p>
      {concept.formula ? (
        <Formula tex={concept.formula} className="rounded-sm border border-border bg-surface-1 px-2" />
      ) : null}
      {concept.diagram ? (
        <MiniDiagram id={concept.diagram} className="rounded-sm border border-border bg-surface-1 p-1" />
      ) : null}
      {rows.length ? (
        <dl className="grid grid-cols-[auto_1fr] items-baseline gap-x-2.5 gap-y-2 text-sm">
          {rows.map(({ key, label, icon: Icon, tone, text }) => (
            <div key={key} className="contents">
              <dt className={cn("flex items-center gap-1 self-start pt-0.5 text-xs font-semibold", tone)}>
                <Icon className="size-3.5" aria-hidden="true" />
                {label}
              </dt>
              <dd className="leading-snug text-text-2">{text}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {concept.links.length ? (
        <ul className="grid gap-1 border-t border-border pt-3">
          {concept.links.map((link) => {
            const kind = LINK_KIND[link.kind];
            const KindIcon = kind.icon;
            return (
              <li key={`${link.kind}:${link.url}:${link.label}`}>
                <a
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group flex items-start gap-2 rounded-sm px-1 py-0.5 text-sm text-link hover:bg-surface-3"
                >
                  <KindIcon className="mt-0.5 size-3.5 shrink-0 text-text-3" aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="sr-only">{kind.label}: </span>
                    <span className="group-hover:underline">{link.label}</span>
                    <span className="sr-only"> (opens in a new tab)</span>
                  </span>
                  <ExternalLink className="mt-0.5 size-3 shrink-0 text-text-3" aria-hidden="true" />
                </a>
              </li>
            );
          })}
        </ul>
      ) : null}
    </>
  );
}
