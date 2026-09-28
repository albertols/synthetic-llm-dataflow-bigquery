import {
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleSlash,
  CircleX,
  Info,
  LoaderCircle,
  OctagonAlert,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/cn";

/**
 * Metric statuses (evaluation_metrics.status), run statuses and scope
 * statuses (evaluation_data_history.status / scope_status), any case.
 */
export type StatusValue =
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
  not_evaluated: { tone: "neutral", label: "Not evaluated", icon: CircleDashed },
  running: { tone: "info", label: "Running", icon: LoaderCircle },
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
  size?: "sm" | "md";
  className?: string;
};

/** Status as icon + label + tone: never colour alone. Tones are the reserved status palette. */
export function StatusPill({ status, label, tone, size = "md", className }: StatusPillProps) {
  const known = describeStatus(status);
  const unknownStatus = known.icon === CircleHelp && known.label === status;
  const meta = tone ? { ...known, tone, icon: unknownStatus ? TONE_ICON[tone] : known.icon } : known;
  const Icon = meta.icon;
  const spinning = meta.icon === LoaderCircle;
  return (
    <span
      data-status={status}
      data-tone={meta.tone}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border font-medium whitespace-nowrap",
        size === "sm" ? "px-1.5 py-px text-[11px] [&_svg]:size-3" : "px-2 py-0.5 text-xs [&_svg]:size-3.5",
        TONE_CLASS[meta.tone],
        className,
      )}
    >
      <Icon aria-hidden="true" className={cn(spinning && "motion-safe:animate-spin")} />
      {label ?? meta.label}
    </span>
  );
}
