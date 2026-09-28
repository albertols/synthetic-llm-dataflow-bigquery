/**
 * The hero: the nine pipeline stages on one rail, from the source table to
 * the evaluation registry. On load (unless the reader asked for reduced
 * motion) a packet walks the rail once and each stage lights up as it
 * arrives, then the rail rests fully lit — the same picture the reduced-motion
 * reader gets at once. Pause, play, step and replay are buttons; hovering or
 * focusing a stage pauses and selects it. Each stage links to the tab that
 * explores it.
 *
 * ≥ 1280 px the rail is horizontal (an SVG under a nine-column grid); below,
 * it runs down the left edge of a vertical list.
 */
import { animate, m, useMotionValue, useTransform } from "motion/react";
import { ArrowRight, ChevronLeft, ChevronRight, Pause, Play, RotateCcw } from "lucide-react";
import { useEffect, useId, useState } from "react";

import type { Facets } from "@contracts/api";

import { InfoHint } from "@/components/InfoHint";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { repoBlobUrl } from "@/lib/links";
import { useReducedMotion } from "@/lib/motion";

import { JOBS, KIND_META, PHASES, STAGES, type Stage, type StageContext } from "../content/stages";
import { TargetLink, targetTabName } from "../content/targets";
import { ExternalAnchor, InlineCode } from "./primitives";

const LAST = STAGES.length - 1;
const FIRST_DWELL_MS = 1400;
const DWELL_MS = 1250;

type Mode = "playing" | "paused" | "done";

/** The x of stage `i`'s station, as a percentage of the rail. */
const stationPct = (i: number) => ((i + 0.5) / STAGES.length) * 100;

export function PipelineHero({ facets }: { facets?: Facets | undefined }) {
  const reduced = useReducedMotion();
  const [active, setActive] = useState(0);
  const [mode, setMode] = useState<Mode>("playing");
  const effective: Mode = reduced ? "done" : mode;

  // The walk: one step per dwell, then rest fully lit.
  useEffect(() => {
    if (effective !== "playing") return;
    const timer = window.setTimeout(
      () => {
        if (active >= LAST) setMode("done");
        else setActive(active + 1);
      },
      active === 0 ? FIRST_DWELL_MS : DWELL_MS,
    );
    return () => window.clearTimeout(timer);
  }, [active, effective]);

  const context: StageContext = { facets };
  const lit = (i: number) => effective === "done" || i <= active;

  const select = (i: number) => {
    setActive(i);
    if (mode === "playing") setMode("paused");
  };
  const step = (delta: number) => select(Math.min(LAST, Math.max(0, active + delta)));
  const play = () => {
    if (active >= LAST) setActive(0);
    setMode("playing");
  };
  const replay = () => {
    setActive(0);
    setMode("playing");
  };

  const stage = STAGES[active]!;
  const headingId = useId();

  return (
    <section id="pipeline" aria-labelledby={headingId} className="grid scroll-mt-32 gap-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="grid max-w-3xl gap-1.5">
          <p className="font-mono text-[11px] font-semibold tracking-[0.16em] text-accent-text uppercase">
            The pipeline
          </p>
          <h2 id={headingId} className="text-xl font-semibold tracking-tight text-text-1 md:text-2xl">
            From a source table to an evaluated synthetic table, in nine steps
          </h2>
          <p className="text-sm leading-relaxed text-text-2 md:text-[15px]">
            Point at a step to read it, or open it in the tab that explores it.
          </p>
        </div>
        <PipelineControls
          mode={effective}
          reduced={reduced}
          active={active}
          onPause={() => setMode("paused")}
          onPlay={play}
          onReplay={replay}
          onStep={step}
        />
      </div>

      <div className="grid gap-2">
        <JobBands />
        <PhaseBands />
        <Rail active={active} mode={effective} reduced={reduced} />
        <ol aria-label="Pipeline stages" className="grid xl:grid-cols-9 xl:gap-2">
          {STAGES.map((item, i) => (
            <StageCard
              key={item.id}
              stage={item}
              index={i}
              context={context}
              lit={lit(i)}
              nextLit={i < LAST && lit(i + 1)}
              selected={i === active}
              onSelect={() => select(i)}
            />
          ))}
        </ol>
      </div>

      <KindLegend />
      <StageDetail stage={stage} index={active} context={context} />
    </section>
  );
}

function PipelineControls({
  mode,
  reduced,
  active,
  onPause,
  onPlay,
  onReplay,
  onStep,
}: {
  mode: Mode;
  reduced: boolean;
  active: number;
  onPause: () => void;
  onPlay: () => void;
  onReplay: () => void;
  onStep: (delta: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Pipeline walk-through">
      <Button
        size="icon-sm"
        variant="outline"
        aria-label="Previous step"
        disabled={active === 0}
        onClick={() => onStep(-1)}
      >
        <ChevronLeft aria-hidden="true" />
      </Button>
      <Button
        size="icon-sm"
        variant="outline"
        aria-label="Next step"
        disabled={active === LAST}
        onClick={() => onStep(1)}
      >
        <ChevronRight aria-hidden="true" />
      </Button>
      {reduced ? (
        <span className="text-xs text-text-3">Animation off: your system asks for reduced motion.</span>
      ) : mode === "playing" ? (
        <Button size="sm" variant="secondary" onClick={onPause}>
          <Pause aria-hidden="true" />
          Pause
        </Button>
      ) : mode === "paused" ? (
        <Button size="sm" variant="secondary" onClick={onPlay}>
          <Play aria-hidden="true" />
          Play
        </Button>
      ) : (
        <Button size="sm" variant="secondary" onClick={onReplay}>
          <RotateCcw aria-hidden="true" />
          Replay
        </Button>
      )}
    </div>
  );
}

function KindLegend() {
  const kinds = ["store", "launcher", "gpu", "beam"] as const;
  return (
    <ul aria-label="What runs each step" className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-text-2">
      {kinds.map((kind) => (
        <li key={kind} className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-full" style={{ background: KIND_META[kind].color }} />
          {kind === "launcher" ? "CPU (launcher or worker)" : KIND_META[kind].label}
        </li>
      ))}
    </ul>
  );
}

/** Which job each stage runs in (desktop rail only; the list repeats it in text). */
function JobBands() {
  return (
    <div aria-hidden="true" className="hidden grid-cols-9 gap-2 xl:grid">
      {JOBS.map((job) => (
        <div
          key={job.label}
          style={{ gridColumn: `span ${job.span} / span ${job.span}` }}
          className={cn(
            "flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs",
            job.isNew ? "border-accent/50 bg-accent-soft text-accent-text" : "border-border bg-surface-1 text-text-2",
          )}
        >
          <span className="font-semibold text-text-1">{job.label}</span>
          <span className="truncate">{job.detail}</span>
          {job.isNew ? (
            <Badge variant="accent" className="ml-auto">
              new
            </Badge>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function PhaseBands() {
  return (
    <div aria-hidden="true" className="hidden grid-cols-9 gap-2 xl:grid">
      {PHASES.map((phase) => (
        <div
          key={phase.id}
          style={{ gridColumn: `span ${phase.span} / span ${phase.span}` }}
          className="flex items-baseline gap-2 border-b border-border-strong px-1 pb-1"
        >
          <span className="text-xs font-semibold text-text-1">{phase.label}</span>
          <span className="text-[11px] text-text-3">{phase.where}</span>
        </div>
      ))}
    </div>
  );
}

/** The horizontal rail: a base line, the lit progress, one station per stage and the travelling packet. */
function Rail({ active, mode, reduced }: { active: number; mode: Mode; reduced: boolean }) {
  const target = mode === "done" ? stationPct(LAST) : stationPct(active);
  const progress = useMotionValue(target);
  const x = useTransform(progress, (v) => `${v}%`);
  useEffect(() => {
    if (reduced) {
      progress.set(target);
      return;
    }
    const controls = animate(progress, target, { duration: 0.8, ease: [0.22, 1, 0.36, 1] });
    return () => controls.stop();
  }, [progress, target, reduced]);

  return (
    <svg aria-hidden="true" className="hidden h-8 w-full overflow-visible xl:block" data-testid="pipeline-rail">
      <line
        x1={`${stationPct(0)}%`}
        x2={`${stationPct(LAST)}%`}
        y1="16"
        y2="16"
        stroke="var(--border-strong)"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <m.line
        x1={`${stationPct(0)}%`}
        x2={x}
        y1="16"
        y2="16"
        stroke="var(--accent)"
        strokeWidth="3"
        strokeLinecap="round"
      />
      {STAGES.map((stage, i) => {
        const lit = mode === "done" || i <= active;
        return (
          <circle
            key={stage.id}
            cx={`${stationPct(i)}%`}
            cy="16"
            r={i === active ? 7.5 : 6}
            fill={lit ? KIND_META[stage.kind].color : "var(--surface-1)"}
            stroke={i === active ? "var(--text-1)" : lit ? "var(--surface-1)" : "var(--border-strong)"}
            strokeWidth="2"
            style={{ transition: "fill 240ms ease, r 240ms ease" }}
          />
        );
      })}
      {mode === "playing" ? (
        <m.circle cx={x} cy="16" r="4" fill="var(--text-1)" style={{ filter: "drop-shadow(0 0 6px var(--accent))" }} />
      ) : null}
    </svg>
  );
}

function StageCard({
  stage,
  index,
  context,
  lit,
  nextLit,
  selected,
  onSelect,
}: {
  stage: Stage;
  index: number;
  context: StageContext;
  lit: boolean;
  nextLit: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  const kind = KIND_META[stage.kind];
  const Icon = stage.icon;
  const descriptionId = useId();
  const target = stage.target(context);
  const phase = PHASES.find((p) => p.id === stage.phase)!;
  const firstOfPhase = STAGES.findIndex((s) => s.phase === stage.phase) === index;
  return (
    <li className="grid grid-cols-[1.75rem_1fr] xl:block" data-stage={stage.id} data-lit={lit} data-selected={selected}>
      {/* Vertical rail (below 1280 px): the segment above, the station, the segment below. */}
      <div aria-hidden="true" className="relative xl:hidden">
        {index > 0 ? (
          <span
            className={cn(
              "absolute top-0 left-1/2 h-1/2 w-0.5 -translate-x-1/2 transition-colors duration-500",
              lit ? "bg-accent" : "bg-border-strong",
            )}
          />
        ) : null}
        {index < LAST ? (
          <span
            className={cn(
              "absolute bottom-0 left-1/2 h-1/2 w-0.5 -translate-x-1/2 transition-colors duration-500",
              nextLit ? "bg-accent" : "bg-border-strong",
            )}
          />
        ) : null}
        <span
          className={cn(
            "absolute top-1/2 left-1/2 -translate-1/2 rounded-full border-2 transition-all duration-300",
            selected ? "size-4 border-text-1" : "size-3 border-surface-1",
          )}
          style={{ background: lit ? kind.color : "var(--surface-3)" }}
        />
      </div>

      <div className="grid gap-1.5 py-1.5 xl:h-full xl:py-0">
        {firstOfPhase ? (
          <p className="flex items-baseline gap-2 px-1 pt-2 text-xs xl:hidden">
            <span className="font-semibold text-text-1">{phase.label}</span>
            <span className="text-text-3">{phase.where}</span>
          </p>
        ) : null}
        <div
          onPointerEnter={(event) => {
            if (event.pointerType !== "touch") onSelect();
          }}
          onFocus={onSelect}
          className={cn(
            "relative flex h-full flex-col gap-1.5 rounded-lg border bg-surface-1 p-3 transition-[border-color,background-color,box-shadow] duration-300",
            "has-[a:focus-visible]:outline-2 has-[a:focus-visible]:outline-offset-2 has-[a:focus-visible]:outline-focus-ring",
            selected
              ? "border-accent bg-surface-2 shadow-[0_0_0_1px_var(--accent),0_8px_24px_-12px_var(--accent)]"
              : lit
                ? "border-border-strong"
                : "border-border bg-bg",
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5">
              <span className="font-mono text-[11px] text-text-3">{String(index + 1).padStart(2, "0")}</span>
              {stage.isNew ? (
                <Badge variant="accent" className="px-1.5 py-0 text-[10px]">
                  new
                </Badge>
              ) : null}
            </span>
            <span className="relative z-10 -my-1 -mr-1">
              <InfoHint concept={stage.concept} />
            </span>
          </div>
          <p className={cn("flex min-w-0 items-center gap-1.5 text-[11px] font-medium", kind.text)}>
            <Icon className="size-3.5 shrink-0" aria-hidden="true" />
            <span className="truncate xl:hidden">{kind.label}</span>
            <span className="hidden truncate xl:inline">{kind.short}</span>
          </p>
          <h3 className="text-sm leading-snug font-semibold text-text-1">
            <TargetLink
              to={target}
              aria-describedby={descriptionId}
              className="outline-none after:absolute after:inset-0 after:rounded-lg after:content-['']"
            >
              {stage.title}
            </TargetLink>
          </h3>
          <p className="text-xs leading-snug text-text-2 xl:hidden">{stage.summary}</p>
          <ul className="mt-auto flex flex-wrap gap-1 pt-1">
            {stage.chips(context).map((chip) => (
              <li
                key={chip}
                className="rounded-sm bg-surface-3 px-1.5 py-0.5 font-mono text-[10.5px] leading-tight text-text-2"
              >
                {chip}
              </li>
            ))}
          </ul>
          <p id={descriptionId} className="sr-only">
            Step {index + 1} of {STAGES.length}, {phase.label}. {stage.summary} Opens the {targetTabName(target)} tab.
          </p>
        </div>
      </div>
    </li>
  );
}

function sourceHref(source: string, anchor?: string) {
  return `${repoBlobUrl(source)}${anchor ? `#${anchor}` : ""}`;
}

/** The selected stage, in full: its quote, what runs it, and where to explore it. */
function StageDetail({ stage, index, context }: { stage: Stage; index: number; context: StageContext }) {
  const kind = KIND_META[stage.kind];
  const phase = PHASES.find((p) => p.id === stage.phase)!;
  const target = stage.target(context);
  const Icon = stage.icon;
  return (
    <div className="relative min-h-44 overflow-hidden rounded-lg border border-border bg-surface-1">
      <span aria-hidden="true" className="absolute inset-y-0 left-0 w-1" style={{ background: kind.color }} />
      <div className="grid gap-4 p-4 pl-5 md:grid-cols-[1fr_auto] md:p-5 md:pl-6" data-testid="stage-detail">
        <div className="grid min-w-0 gap-2">
          <p className="font-mono text-[11px] text-text-3">
            Step {index + 1} of {STAGES.length} · {phase.label} · {phase.where}
          </p>
          <h3 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-text-1">
            <Icon className={cn("size-5 shrink-0", kind.text)} aria-hidden="true" />
            {stage.title}
            <InfoHint concept={stage.concept} size="md" />
          </h3>
          <p className="text-sm text-text-2">{stage.summary}</p>
          <blockquote className="border-l-2 border-border-strong pl-3 text-sm leading-relaxed text-text-1">
            <p>
              “<InlineCode text={stage.quote.text} />”
            </p>
            <footer className="mt-1 text-xs text-text-3">
              —{" "}
              <ExternalAnchor href={sourceHref(stage.quote.source, stage.quote.anchor)}>
                {stage.quote.source}
              </ExternalAnchor>
            </footer>
          </blockquote>
        </div>
        <div className="flex flex-col items-start gap-3 md:items-end md:justify-between">
          <span className={cn("inline-flex items-center gap-1.5 text-xs font-medium", kind.text)}>
            <span aria-hidden="true" className="size-2 rounded-full" style={{ background: kind.color }} />
            {kind.label}
          </span>
          <Button asChild variant="primary" size="sm">
            <TargetLink to={target}>
              Open {targetTabName(target)}
              <ArrowRight aria-hidden="true" />
            </TargetLink>
          </Button>
        </div>
      </div>
    </div>
  );
}
