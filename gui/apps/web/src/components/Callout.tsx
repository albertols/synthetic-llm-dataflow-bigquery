/**
 * Callout — an inline note with a tone, an icon and a visible label.
 *
 *   <Callout tone="docs-differ" title="Docs differ">
 *     DESIGN.md says 11-point deciles; b1 interpolates the full sample. The chart shows the code.
 *   </Callout>
 *   <Banner tone="warn" title="3-D view unavailable" onDismiss={…}>Showing the 2-D projection.</Banner>
 *
 * Tones: info (blue), warn (amber), danger (red), docs-differ (Beam orange,
 * "where docs and code disagree, the UI shows the code"). The tone is never
 * colour-only: each carries its icon and, unless `title` replaces it, a
 * visible tone label. `live` announces a callout that appears after load
 * (role="status"); static callouts are role="note".
 */
import { FileDiff, Info, OctagonAlert, TriangleAlert, X, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type CalloutTone = "info" | "warn" | "danger" | "docs-differ";

const TONES: Record<CalloutTone, { icon: LucideIcon; label: string; box: string; ink: string }> = {
  info: { icon: Info, label: "Note", box: "border-status-info/45 bg-status-info/10", ink: "text-status-info-text" },
  warn: {
    icon: TriangleAlert,
    label: "Warning",
    box: "border-status-warn/45 bg-status-warn/10",
    ink: "text-status-warn-text",
  },
  danger: {
    icon: OctagonAlert,
    label: "Problem",
    box: "border-status-critical/55 bg-status-critical/12",
    ink: "text-status-critical-text",
  },
  "docs-differ": {
    icon: FileDiff,
    label: "Docs differ",
    box: "border-accent/45 bg-accent-soft",
    ink: "text-accent-text",
  },
};

export type CalloutProps = {
  tone?: CalloutTone;
  /** Bold lead-in; defaults to the tone label ("Note", "Warning", "Problem", "Docs differ"). */
  title?: string;
  children?: ReactNode;
  /** A link or button after the text. */
  action?: ReactNode;
  /** Announce it (role="status") — for callouts that appear after the page loaded. */
  live?: boolean;
  /** Shows a close button (banners). */
  onDismiss?: () => void;
  className?: string;
};

export function Callout({ tone = "info", title, children, action, live = false, onDismiss, className }: CalloutProps) {
  const meta = TONES[tone];
  const Icon = meta.icon;
  return (
    <div
      role={live ? "status" : "note"}
      data-tone={tone}
      className={cn(
        "relative flex items-start gap-3 rounded-md border px-3.5 py-3 text-sm",
        meta.box,
        onDismiss && "pr-10",
        className,
      )}
    >
      <Icon className={cn("mt-0.5 size-4 shrink-0", meta.ink)} aria-hidden="true" />
      <div className="grid min-w-0 flex-1 gap-1">
        <p className={cn("font-semibold", meta.ink)}>{title ?? meta.label}</p>
        {children ? <div className="leading-relaxed text-text-2">{children}</div> : null}
        {action ? <div className="mt-1">{action}</div> : null}
      </div>
      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          aria-label={`Dismiss: ${title ?? meta.label}`}
          className="absolute top-2 right-2 inline-flex size-7 cursor-pointer items-center justify-center rounded-sm text-text-3 hover:bg-surface-3 hover:text-text-1"
        >
          <X className="size-3.5" aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
}

/** A full-width callout for page- or frame-level state (WebGL fallback, sampled mode, stale data). Live by default. */
export function Banner({ className, live = true, ...props }: CalloutProps) {
  return <Callout live={live} className={cn("w-full rounded-lg", className)} {...props} />;
}
