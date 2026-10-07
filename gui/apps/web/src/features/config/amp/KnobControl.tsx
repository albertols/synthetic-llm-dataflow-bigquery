/**
 * One knob on the amp, drawn by its kind (model/knobs.ts `kindOf`):
 *
 *   dial / selector  a rotary (role="slider"): arrows turn it, Page Up/Down
 *                    jump, Home/End go to the ends, Enter or a click opens its
 *                    sheet; a mouse or pen drags it vertically.
 *   screw            a fixed screw: a button that opens the sheet and says
 *                    "fixed constant" — there is nothing to turn.
 *   readout          a derived value's LED readout (button → sheet).
 *   port             a jack for a free-form flag (button → sheet).
 *
 * Every part carries a visible text label: the panel never relies on a
 * pictogram (see gui/BRANDING.md, the amp's trade-dress rule).
 */
import { useId, useRef, type KeyboardEvent, type PointerEvent } from "react";

import type { Knob, KnobValue } from "@contracts/knobs";

import { cn } from "@/lib/cn";

import { formatKnobValue, kindOf, positions, SETTABLE_LABEL, shortValue, type KnobKind } from "../model/knobs";

const SWEEP = 270;
const START = -135;
const DRAG_PX_PER_STEP = 14;

export type KnobControlProps = {
  knob: Knob;
  value: KnobValue;
  onChange: (value: KnobValue) => void;
  onOpen: () => void;
  /** Marks the knobs that feed the scenario (a small "live" tick). */
  live?: boolean;
};

/** Accessible description of how a knob can be set. */
export function settableText(k: Knob, kind: KnobKind = kindOf(k)): string {
  if (kind === "screw") return "Fixed constant: not settable, change it in code.";
  if (kind === "readout") return "Derived from other settings: not settable directly.";
  const via = k.settable_via.map((v) => SETTABLE_LABEL[v] ?? v).join(", ");
  return kind === "port" ? `Settable via ${via}; free-form, not turnable here.` : `Settable via ${via}.`;
}

export function KnobControl({ knob: k, value, onChange, onOpen, live = false }: KnobControlProps) {
  const kind = kindOf(k);
  const labelId = useId();
  const descId = useId();
  const pos = positions(k, value);
  const changed = JSON.stringify(value) !== JSON.stringify(k.value);

  return (
    <div
      data-knob={k.id}
      data-kind={kind}
      className="group/knob relative flex w-[6.25rem] shrink-0 flex-col items-center gap-1.5 rounded-lg px-1.5 pt-2 pb-2.5 text-center sm:w-[7.25rem]"
    >
      {kind === "dial" || kind === "selector" ? (
        <Rotary
          knob={k}
          kind={kind}
          values={pos!.values}
          index={pos!.index}
          labelId={labelId}
          descId={descId}
          onChange={onChange}
          onOpen={onOpen}
        />
      ) : (
        <button
          type="button"
          onClick={onOpen}
          aria-label={`${k.label}: ${formatKnobValue(k, value)}`}
          aria-describedby={descId}
          className="flex size-16 cursor-pointer items-center justify-center rounded-full focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
        >
          <FixedPart kind={kind} value={shortValue(k, value)} />
        </button>
      )}
      <span id={labelId} className="line-clamp-3 min-h-[2.1rem] text-[11px] leading-tight font-medium text-text-1">
        {k.label}
      </span>
      <span
        className={cn(
          "max-w-full truncate rounded-sm px-1 font-mono text-[11px] tabular-nums",
          changed ? "bg-accent-soft text-accent-text" : "text-text-2",
        )}
        aria-hidden="true"
      >
        {shortValue(k, value)}
      </span>
      <span id={descId} className="sr-only">
        {settableText(k, kind)} {changed ? `Changed from the code default ${formatKnobValue(k)}.` : ""} Enter opens its
        details.
      </span>
      <span className="flex flex-wrap justify-center gap-0.5" aria-hidden="true">
        {k.settable_via.map((via) => (
          <Chip key={via}>{SETTABLE_LABEL[via] ?? via}</Chip>
        ))}
        {live ? <Chip tone="live">scenario</Chip> : null}
      </span>
    </div>
  );
}

function Chip({ children, tone }: { children: string; tone?: "live" }) {
  return (
    <span
      className={cn(
        "rounded-[3px] border px-1 text-[9px] leading-[14px] font-semibold tracking-wide uppercase",
        tone === "live" ? "border-accent/50 text-accent-text" : "border-border-strong text-text-3",
      )}
    >
      {children}
    </span>
  );
}

type RotaryProps = {
  knob: Knob;
  kind: "dial" | "selector";
  values: KnobValue[];
  index: number;
  labelId: string;
  descId: string;
  onChange: (value: KnobValue) => void;
  onOpen: () => void;
};

function Rotary({ knob: k, kind, values, index, labelId, descId, onChange, onOpen }: RotaryProps) {
  const drag = useRef<{ y: number; index: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const last = values.length - 1;
  const fraction = last > 0 ? index / last : 0;
  const angle = START + fraction * SWEEP;
  const current = values[index] ?? null;

  const go = (next: number) => {
    const clamped = Math.max(0, Math.min(last, next));
    if (clamped !== index) onChange(values[clamped] ?? null);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const big = Math.max(1, Math.round(values.length / 5));
    const keys: Record<string, () => void> = {
      ArrowUp: () => go(index + 1),
      ArrowRight: () => go(index + 1),
      ArrowDown: () => go(index - 1),
      ArrowLeft: () => go(index - 1),
      PageUp: () => go(index + big),
      PageDown: () => go(index - big),
      Home: () => go(0),
      End: () => go(last),
      Enter: onOpen,
    };
    const action = keys[event.key];
    if (!action) return;
    event.preventDefault();
    action();
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    // Touch scrolls the page; a tap opens the sheet, where a slider turns the knob.
    if (event.pointerType === "touch" || event.button !== 0) return;
    drag.current = { y: event.clientY, index, moved: false };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const delta = Math.round((d.y - event.clientY) / DRAG_PX_PER_STEP);
    if (Math.abs(d.y - event.clientY) > 4) d.moved = true;
    const next = Math.max(0, Math.min(last, d.index + delta));
    if (next !== index) onChange(values[next] ?? null);
  };
  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    suppressClick.current = Boolean(d?.moved);
  };
  const onClick = () => {
    if (suppressClick.current) {
      suppressClick.current = false;
      return;
    }
    onOpen();
  };

  const numeric = kind === "dial";
  const ticks = kind === "selector" ? values.length : 11;
  const first = values[0];
  const lastValue = values[last];
  return (
    <div
      role="slider"
      tabIndex={0}
      aria-labelledby={labelId}
      aria-describedby={descId}
      aria-valuemin={numeric && typeof first === "number" ? first : 0}
      aria-valuemax={numeric && typeof lastValue === "number" ? lastValue : last}
      aria-valuenow={numeric && typeof current === "number" ? current : index}
      aria-valuetext={formatKnobValue(k, current)}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onClick={onClick}
      className="relative size-16 cursor-grab touch-pan-y rounded-full select-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring active:cursor-grabbing"
    >
      <svg viewBox="0 0 64 64" className="size-16" aria-hidden="true">
        {/* Scale: detents for a selector, 11 ticks for a dial. */}
        {Array.from({ length: ticks }, (_, i) => {
          const at = ticks > 1 ? i / (ticks - 1) : 0;
          const a = ((START + at * SWEEP - 90) * Math.PI) / 180;
          const lit = at <= fraction + 1e-9;
          return (
            <line
              key={i}
              x1={32 + Math.cos(a) * 27}
              y1={32 + Math.sin(a) * 27}
              x2={32 + Math.cos(a) * 31}
              y2={32 + Math.sin(a) * 31}
              stroke={lit ? "var(--accent)" : "var(--border-strong)"}
              strokeWidth={kind === "selector" ? 2.5 : 1.5}
              strokeLinecap="round"
            />
          );
        })}
        <circle cx="32" cy="32" r="22" fill="var(--surface-3)" stroke="var(--control-border)" strokeWidth="1.5" />
        <circle cx="32" cy="32" r="17" fill="var(--surface-2)" stroke="var(--border-strong)" strokeWidth="1" />
        <g
          style={{ transform: `rotate(${angle}deg)`, transformOrigin: "32px 32px" }}
          className="transition-transform duration-150 motion-reduce:transition-none"
        >
          <line x1="32" y1="32" x2="32" y2="13" stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" />
        </g>
        <circle cx="32" cy="32" r="3" fill="var(--control-border)" />
      </svg>
    </div>
  );
}

/** Screws, readouts and jacks: not turnable, text-labelled next to them. */
function FixedPart({ kind, value }: { kind: KnobKind; value: string }) {
  if (kind === "screw")
    return (
      <svg viewBox="0 0 64 64" className="size-16" aria-hidden="true">
        <circle cx="32" cy="32" r="14" fill="var(--surface-3)" stroke="var(--control-border)" strokeWidth="1.5" />
        <circle cx="32" cy="32" r="11" fill="none" stroke="var(--border-strong)" strokeWidth="1" />
        <line x1="24" y1="28" x2="40" y2="36" stroke="var(--text-3)" strokeWidth="3" strokeLinecap="round" />
        <text x="32" y="60" fill="var(--text-3)" fontSize="7.5" textAnchor="middle" letterSpacing="1">
          FIXED
        </text>
      </svg>
    );
  if (kind === "readout")
    return (
      <svg viewBox="0 0 64 64" className="size-16" aria-hidden="true">
        <rect x="4" y="20" width="56" height="24" rx="4" fill="var(--bg)" stroke="var(--border-strong)" />
        <text
          x="32"
          y="36"
          fill="var(--accent-text)"
          fontSize="9"
          textAnchor="middle"
          fontFamily="var(--font-mono, monospace)"
        >
          {value}
        </text>
      </svg>
    );
  // port: a jack.
  return (
    <svg viewBox="0 0 64 64" className="size-16" aria-hidden="true">
      <circle cx="32" cy="32" r="14" fill="var(--surface-3)" stroke="var(--control-border)" strokeWidth="1.5" />
      <circle cx="32" cy="32" r="6" fill="var(--bg)" stroke="var(--border-strong)" strokeWidth="1.5" />
    </svg>
  );
}
