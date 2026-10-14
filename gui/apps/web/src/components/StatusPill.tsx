import {
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleSlash,
  CircleX,
  Info,
  LoaderCircle,
  OctagonAlert,
  Scale,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/cn";

/**
 * Metric statuses (evaluation_metrics.status), run statuses and scope
 * statuses (evaluation_data_history.status / scope_status), any case; and two
 * display states no table holds: STALE, a RUNNING event that is too old to be
 * running (lib/runState.ts), and `adjusted`, the generator's reading of a key
 * the launch adjusted (ADR 0038, lib/adjustments.ts).
 */
export type StatusValue =
  | "adjusted"
  | "ok"
  | "count_mismatch"
  | "contaminated"
  | "expired"
  | "empty"
  | "unknown"
  | "pass"
  | "warn"
  | "fail"
  | "info"
  | "not_evaluated"
  | "RUNNING"
  | "STALE"
  | "SUCCEEDED"
  | "SUCCEEDED_WITH_WARNINGS"
  | "PARTIAL"
  | "SKIPPED"
  | "FAILED";

export type StatusTone = "good" | "warn" | "serious" | "critical" | "info" | "neutral";

const STATUS: Record<string, { tone: StatusTone; label: string; icon: LucideIcon }> = {
  pass: { tone: "good", label: "Pass", icon: CircleCheck },
  warn: { tone: "warn", label: "Warn", icon: TriangleAlert },
  fail: { tone: "critical", label: "Fail", icon: CircleX },
  info: { tone: "info", label: "Info", icon: Info },
  // A key the launch adjusted (ADR 0038): the generator's reading, weighed against the source.
  // Never stored by the evaluator; the caller sets the tone (info: faithful copy, warn: it missed).
  adjusted: { tone: "info", label: "Adjusted", icon: Scale },
  not_evaluated: { tone: "neutral", label: "Not evaluated", icon: CircleDashed },
  running: { tone: "info", label: "Running", icon: LoaderCircle },
  // Needs attention: neither a success nor a failure the evaluator measured. Not the spinner, and not
  // the triangle of "Succeeded with warnings". On purpose it shares its icon with the run status Skipped
  // (neutral: nothing was measured there either) and its whole look with the scope status "Expired".
  stale: { tone: "warn", label: "Stale", icon: CircleSlash },
  succeeded: { tone: "good", label: "Succeeded", icon: CircleCheck },
  succeeded_with_warnings: { tone: "warn", label: "Succeeded with warnings", icon: TriangleAlert },
  partial: { tone: "serious", label: "Partial", icon: OctagonAlert },
  skipped: { tone: "neutral", label: "Skipped", icon: CircleSlash },
  failed: { tone: "critical", label: "Failed", icon: CircleX },
  // Scope statuses (evaluation_data_history.scope_status).
  ok: { tone: "good", label: "Scope OK", icon: CircleCheck },
  count_mismatch: { tone: "warn", label: "Count mismatch", icon: TriangleAlert },
  contaminated: { tone: "critical", label: "Contaminated", icon: OctagonAlert },
  expired: { tone: "warn", label: "Expired", icon: CircleSlash },
  empty: { tone: "serious", label: "Empty", icon: CircleDashed },
  unknown: { tone: "neutral", label: "Unknown", icon: CircleHelp },
};

/** The icon a tone uses when the status itself is unknown to the map. */
const TONE_ICON: Record<StatusTone, LucideIcon> = {
  good: CircleCheck,
  warn: TriangleAlert,
  serious: OctagonAlert,
  critical: CircleX,
  info: Info,
  neutral: CircleHelp,
};

const TONE_CLASS: Record<StatusTone, string> = {
  good: "border-status-good/45 bg-status-good/12 text-status-good-text",
  warn: "border-status-warn/45 bg-status-warn/12 text-status-warn-text",
  serious: "border-status-serious/45 bg-status-serious/12 text-status-serious-text",
  critical: "border-status-critical/55 bg-status-critical/14 text-status-critical-text",
  info: "border-status-info/50 bg-status-info/14 text-status-info-text",
  neutral: "border-status-neutral/50 bg-status-neutral/14 text-status-neutral-text",
};

/** Whether the status map knows this value (any case). */
function isKnownStatus(status: string): boolean {
  return Object.hasOwn(STATUS, status.trim().toLowerCase());
}

/** Tone and default label for a status string; unknown values fall back to neutral with the raw text. */
export function describeStatus(status: string): { tone: StatusTone; label: string; icon: LucideIcon } {
  return STATUS[status.trim().toLowerCase()] ?? { tone: "neutral", label: status, icon: CircleHelp };
}

export type StatusPillProps = {
  status: StatusValue | (string & {});
  /** Overrides the default label ("Pass", "Succeeded with warnings" …). */
  label?: string;
  /** Overrides the tone (and, for statuses the map does not know, the icon). */
  tone?: StatusTone;
  /** A longer reading: the hover text, also read by a screen reader after the label. */
  title?: string;
  size?: "sm" | "md";
  className?: string;
};

/** Status as icon + label + tone: never colour alone. Tones are the reserved status palette. */
export function StatusPill({ status, label, tone, title, size = "md", className }: StatusPillProps) {
  const known = describeStatus(status);
  const meta = tone ? { ...known, tone, icon: isKnownStatus(status) ? known.icon : TONE_ICON[tone] } : known;
  const Icon = meta.icon;
  const spinning = meta.icon === LoaderCircle;
  return (
    <span
      data-status={status}
      data-tone={meta.tone}
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border font-medium whitespace-nowrap",
        size === "sm" ? "px-1.5 py-px text-[11px] [&_svg]:size-3" : "px-2 py-0.5 text-xs [&_svg]:size-3.5",
        TONE_CLASS[meta.tone],
        className,
      )}
    >
      <Icon aria-hidden="true" className={cn(spinning && "motion-safe:animate-spin")} />
      {label ?? meta.label}
      {title ? <span className="sr-only"> ({title})</span> : null}
    </span>
  );
}
