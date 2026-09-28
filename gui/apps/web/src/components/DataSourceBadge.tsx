import { Database, FlaskConical } from "lucide-react";

import { cn } from "@/lib/cn";
import type { DataSource } from "@/lib/dataSource";

import { InfoHint } from "./InfoHint";

/** MOCK or BIGQUERY <project>: always visible in the top nav, with its (i). */
export function DataSourceBadge({ source, className }: { source: DataSource; className?: string }) {
  const isMock = source.mode === "mock";
  const Icon = isMock ? FlaskConical : Database;
  return (
    <span className={cn("inline-flex items-center gap-0.5", className)}>
      <span
        data-mode={source.mode}
        className={cn(
          "inline-flex h-7 max-w-44 items-center gap-1.5 rounded-full border px-2.5 font-mono text-[11px] font-semibold tracking-wide",
          isMock ? "border-border-strong bg-surface-3 text-text-2" : "border-brand-blue/50 bg-info-soft text-link",
        )}
      >
        <Icon className="size-3.5 shrink-0" aria-hidden="true" />
        <span className="sr-only">Data source: </span>
        <span className="truncate">{isMock ? "MOCK" : `BIGQUERY ${source.project}`}</span>
      </span>
      <InfoHint concept="core:data-source" side="bottom" />
    </span>
  );
}
