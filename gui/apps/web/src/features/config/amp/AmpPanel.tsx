/**
 * The pipeline amp: an original console front panel — a neutral dark
 * faceplate, Beam-orange accents, every part text-labelled — with one strip
 * per knobs.json channel and a meter bridge that re-computes the scenario as
 * the dials turn. Not a reproduction of any amplifier maker's trade dress:
 * no coloured tolex, no crest, wordmark or slogan, no pictogram-only legends.
 */
import { useEffect, useState } from "react";

import { InfoHint } from "@/components/InfoHint";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/cn";
import { formatCount, formatDuration, formatFixed, formatPercent } from "@/lib/format";

import { CODE_REF_SHORT } from "../model/links";
import { channelKnobs, CHANNELS, KNOBS, kindOf, SCENARIO_KNOBS } from "../model/knobs";
import { useScenario } from "../model/state";
import { KnobControl } from "./KnobControl";

const ALL = "all";

/** Phones start on one channel (84 knobs are a long scroll at 390 px); wider screens see the whole console. */
function defaultChannel(): string {
  const narrow = typeof window !== "undefined" && window.matchMedia?.("(max-width: 639px)").matches;
  return narrow ? (CHANNELS[0]?.id ?? ALL) : ALL;
}

export function AmpPanel({
  onOpenKnob,
  initialChannel,
  onChannelChange,
}: {
  onOpenKnob: (id: string) => void;
  /**
   * A channel id to show first (a deep link: `/config?section=amp&channel=free_text`);
   * an unknown id falls back to "all" on wide screens, the first channel on phones.
   */
  initialChannel?: string;
  /** The channel the reader picks (the page keeps it in the URL, and the panel follows the URL back). */
  onChannelChange?: (channel: string) => void;
}) {
  const { settings, setSetting } = useScenario();
  const known = (id: string | undefined): id is string =>
    id !== undefined && (id === ALL || CHANNELS.some((c) => c.id === id));
  const linked = known(initialChannel);
  const [channel, setChannel] = useState<string>(() => (linked ? initialChannel : defaultChannel()));
  // Follow the URL after mount too: back/forward (or a cross-tab link while the page is open)
  // changes `initialChannel` without remounting. The page writes no channel for ALL, so a URL
  // that loses its channel means ALL; an id this build does not know leaves the pick alone.
  const [followed, setFollowed] = useState(initialChannel);
  if (initialChannel !== followed) {
    setFollowed(initialChannel);
    if (initialChannel === undefined) setChannel(ALL);
    else if (known(initialChannel)) setChannel(initialChannel);
  }
  const shown = channel === ALL ? CHANNELS : CHANNELS.filter((c) => c.id === channel);
  // A link to one channel lands on it: scroll its strip into view after the page lays out,
  // once (the channel the page opened with, not every later pick).
  const [landing] = useState(() => (linked && initialChannel !== ALL ? initialChannel : null));
  useEffect(() => {
    if (!landing) return;
    const frame = requestAnimationFrame(() =>
      document.getElementById(`amp-ch-${landing}`)?.scrollIntoView({ block: "start" }),
    );
    return () => cancelAnimationFrame(frame);
  }, [landing]);
  const counts = {
    turnable: KNOBS.filter((k) => ["dial", "selector"].includes(kindOf(k))).length,
    screws: KNOBS.filter((k) => kindOf(k) === "screw").length,
    planned: KNOBS.filter((k) => kindOf(k) === "planned").length,
  };

  return (
    <section
      aria-labelledby="amp-title"
      className="relative overflow-hidden rounded-xl border border-border-strong bg-[linear-gradient(180deg,var(--surface-2),var(--surface-1)_40%,var(--bg))] shadow-[inset_0_1px_0_rgb(255_255_255/0.04),0_18px_40px_-24px_rgb(0_0_0/0.6)]"
    >
      <div className="h-1 bg-accent" aria-hidden="true" />
      <Screws />
      <header className="flex flex-wrap items-end justify-between gap-3 px-5 pt-4 pb-3 sm:px-8">
        <div className="grid gap-1">
          <div className="flex items-center gap-1.5">
            <h2 id="amp-title" className="font-mono text-sm font-semibold tracking-[0.2em] text-text-1 uppercase">
              Pipeline amp
            </h2>
            <InfoHint concept="config:amp" />
          </div>
          <p className="text-xs text-text-3">
            {KNOBS.length} knobs · {CHANNELS.length} channels · values from the code at{" "}
            <code className="font-mono text-text-2">{CODE_REF_SHORT}</code> · {counts.turnable} turnable ·{" "}
            {counts.screws} fixed screws · {counts.planned} planned
          </p>
        </div>
        <Legend />
      </header>

      <MeterBridge />

      <div className="flex flex-col gap-3 px-5 pt-4 sm:px-8">
        <div className="flex flex-wrap items-center gap-2">
          <span id="amp-channel-label" className="font-mono text-[11px] tracking-widest text-text-3 uppercase">
            Channel
          </span>
          <ToggleGroup
            type="single"
            value={channel}
            onValueChange={(v) => {
              if (!v) return;
              setChannel(v);
              onChannelChange?.(v);
            }}
            aria-labelledby="amp-channel-label"
            className="flex-wrap"
          >
            <ToggleGroupItem value={ALL} className="font-mono text-xs tracking-wide">
              ALL
            </ToggleGroupItem>
            {CHANNELS.map((c) => (
              <ToggleGroupItem key={c.id} value={c.id} className="font-mono text-xs tracking-wide">
                {c.label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
        <p className="text-xs text-text-3">
          Turn a dial: focus it, then arrows, Page Up/Down, Home/End — or drag it with a mouse. Enter or a click opens
          its sheet. Screws are code constants and do not turn.
        </p>
      </div>

      <div className="grid gap-4 p-3 sm:p-8 sm:pt-5">
        {shown.map((c) => (
          <section
            key={c.id}
            aria-labelledby={`amp-ch-${c.id}`}
            data-channel={c.id}
            className="rounded-lg border border-border bg-surface-1/70 shadow-[inset_0_1px_0_rgb(255_255_255/0.03)]"
          >
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-border px-4 py-2.5">
              <h3
                id={`amp-ch-${c.id}`}
                className="border-b-2 border-accent pb-0.5 font-mono text-xs font-semibold tracking-[0.18em] text-text-1 uppercase"
              >
                {c.label}
              </h3>
              <p className="min-w-0 flex-1 text-xs text-text-3">{c.description}</p>
            </div>
            <ChannelGroups channelId={c.id} settings={settings} onChange={setSetting} onOpen={onOpenKnob} />
          </section>
        ))}
      </div>
    </section>
  );
}

function ChannelGroups({
  channelId,
  settings,
  onChange,
  onOpen,
}: {
  channelId: string;
  settings: ReturnType<typeof useScenario>["settings"];
  onChange: ReturnType<typeof useScenario>["setSetting"];
  onOpen: (id: string) => void;
}) {
  const knobs = channelKnobs(channelId);
  const groups = [...new Set(knobs.map((k) => k.group))];
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-3 px-1 py-3 sm:px-2">
      {groups.map((group) => (
        <div key={group} className="grid min-w-0 gap-1" role="group" aria-label={group}>
          <p className="px-2 font-mono text-[10px] tracking-widest text-text-3 uppercase" aria-hidden="true">
            {group}
          </p>
          <div className="flex flex-wrap">
            {knobs
              .filter((k) => k.group === group)
              .map((k) => (
                <KnobControl
                  key={k.id}
                  knob={k}
                  value={settings[k.id] ?? k.value}
                  onChange={(v) => onChange(k.id, v)}
                  onOpen={() => onOpen(k.id)}
                  live={(SCENARIO_KNOBS as readonly string[]).includes(k.id)}
                />
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/** The live outputs, re-computed on every turn. */
export function MeterBridge({ className }: { className?: string }) {
  const { inputs, outputs } = useScenario();
  const meters: Array<{ label: string; value: string; concept?: string }> = [
    { label: "Sample n", value: formatCount(outputs.nEff), concept: "knob:reference_rows_limit" },
    { label: "Target M", value: formatCount(inputs.M), concept: "knob:num_rows" },
    { label: "M / n", value: `${formatCount(Math.round(outputs.ampSample))}×`, concept: "stats:amplification" },
    {
      label: "DKW ε(n)",
      value: outputs.census ? "0 (census)" : formatFixed(outputs.epsilon, 4),
      concept: "stats:dkw",
    },
    { label: "Rows / pool value", value: formatCount(Math.round(outputs.poolReuse)), concept: "stats:pool-reuse" },
    { label: "Stats tier", value: inputs.tier, concept: "config:stats-tier" },
    {
      label: `Gate (${inputs.env})`,
      value: outputs.blockerRatio === null ? "—" : formatPercent(outputs.blockerRatio, 0),
      concept: "config:blocker-gate",
    },
    { label: "Time (est.)", value: formatDuration(outputs.time.totalMin * 60), concept: "config:time-estimate" },
  ];
  return (
    <dl
      data-testid="meter-bridge"
      className={cn(
        "mx-5 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border-strong bg-border sm:mx-8 sm:grid-cols-4 xl:grid-cols-8",
        className,
      )}
    >
      {meters.map((m) => (
        <div key={m.label} className="flex min-w-0 flex-col gap-0.5 bg-bg px-3 py-2">
          <dt className="flex items-start gap-0.5 font-mono text-[10px] leading-tight tracking-wider text-text-3 uppercase">
            <span className="min-w-0 pt-1.5 [overflow-wrap:anywhere]">{m.label}</span>
            {m.concept ? <InfoHint concept={m.concept} /> : null}
          </dt>
          <dd className="truncate font-mono text-base text-accent-text">{m.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Legend() {
  const parts = [
    { label: "dial", d: <DialGlyph /> },
    { label: "switch", d: <DialGlyph switchLike /> },
    { label: "fixed screw", d: <ScrewGlyph /> },
    { label: "readout", d: <ReadoutGlyph /> },
    { label: "jack", d: <JackGlyph /> },
    { label: "planned", d: <PlannedGlyph /> },
  ];
  return (
    <ul className="flex flex-wrap items-center gap-x-3 gap-y-1" aria-label="Panel parts">
      {parts.map((p) => (
        <li key={p.label} className="flex items-center gap-1 text-[11px] text-text-3">
          {p.d}
          {p.label}
        </li>
      ))}
    </ul>
  );
}

const glyph = "size-4 shrink-0";
function DialGlyph({ switchLike = false }: { switchLike?: boolean }) {
  return (
    <svg viewBox="0 0 16 16" className={glyph} aria-hidden="true">
      <circle cx="8" cy="8" r="6.5" fill="var(--surface-3)" stroke="var(--control-border)" />
      <line
        x1="8"
        y1="8"
        x2={switchLike ? 8 : 11.5}
        y2={switchLike ? 2.5 : 4.5}
        stroke="var(--accent)"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}
function ScrewGlyph() {
  return (
    <svg viewBox="0 0 16 16" className={glyph} aria-hidden="true">
      <circle cx="8" cy="8" r="5.5" fill="var(--surface-3)" stroke="var(--control-border)" />
      <line x1="5" y1="6.5" x2="11" y2="9.5" stroke="var(--text-3)" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
function ReadoutGlyph() {
  return (
    <svg viewBox="0 0 16 16" className={glyph} aria-hidden="true">
      <rect x="1" y="4.5" width="14" height="7" rx="1.5" fill="var(--bg)" stroke="var(--border-strong)" />
      <line x1="4" y1="8" x2="12" y2="8" stroke="var(--accent-text)" strokeWidth="1.5" />
    </svg>
  );
}
function PlannedGlyph() {
  return (
    <svg viewBox="0 0 16 16" className={glyph} aria-hidden="true">
      <circle cx="8" cy="8" r="6.5" fill="var(--surface-2)" stroke="var(--control-border)" strokeDasharray="2 2" />
    </svg>
  );
}
function JackGlyph() {
  return (
    <svg viewBox="0 0 16 16" className={glyph} aria-hidden="true">
      <circle cx="8" cy="8" r="5.5" fill="var(--surface-3)" stroke="var(--control-border)" />
      <circle cx="8" cy="8" r="2.2" fill="var(--bg)" stroke="var(--border-strong)" />
    </svg>
  );
}

/** Four decorative rack screws in the faceplate corners. */
function Screws() {
  const at = ["left-2 top-3", "right-2 top-3", "left-2 bottom-2", "right-2 bottom-2"];
  return (
    <>
      {at.map((pos) => (
        <svg key={pos} viewBox="0 0 12 12" className={cn("absolute size-3", pos)} aria-hidden="true">
          <circle cx="6" cy="6" r="5" fill="var(--surface-3)" stroke="var(--border-strong)" />
          <line x1="3" y1="4.5" x2="9" y2="7.5" stroke="var(--text-3)" strokeWidth="1.2" />
        </svg>
      ))}
    </>
  );
}
