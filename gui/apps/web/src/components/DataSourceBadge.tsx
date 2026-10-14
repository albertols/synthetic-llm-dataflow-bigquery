import { CloudOff, Database, FlaskConical, LoaderCircle } from "lucide-react";

import { cn } from "@/lib/cn";
import type { DataSource } from "@/lib/dataSource";

import { InfoHint } from "./InfoHint";

const LABELS = { mock: "MOCK", connecting: "CONNECTING", offline: "OFFLINE" } as const;

/** MOCK or BIGQUERY <project> (from /api/health): always visible in the top nav, with its (i). */
export function DataSourceBadge({ source, className }: { source: DataSource; className?: string }) {
  const Icon =
    source.mode === "bigquery"
      ? Database
      : source.mode === "offline"
        ? CloudOff
        : source.mode === "connecting"
          ? LoaderCircle
          : FlaskConical;
  const text = source.mode === "bigquery" ? `BIGQUERY ${source.project}` : LABELS[source.mode];
  return (
    <span className={cn("inline-flex items-center gap-0.5", className)}>
      <span
        data-mode={source.mode}
        aria-live="polite"
        className={cn(
          "inline-flex h-7 max-w-44 items-center gap-1.5 rounded-full border px-2.5 font-mono text-[11px] font-semibold tracking-wide",
          source.mode === "bigquery"
            ? "border-brand-blue/50 bg-info-soft text-link"
            : source.mode === "offline"
              ? "border-status-serious/50 bg-surface-3 text-status-serious-text"
              : "border-border-strong bg-surface-3 text-text-2",
        )}
      >
        <Icon
          className={cn("size-3.5 shrink-0", source.mode === "connecting" && "motion-safe:animate-spin")}
          aria-hidden="true"
        />
        <span className="sr-only">Data source: </span>
        <span className="truncate">{text}</span>
      </span>
      <InfoHint concept="core:data-source" side="bottom" />
    </span>
  );
}
