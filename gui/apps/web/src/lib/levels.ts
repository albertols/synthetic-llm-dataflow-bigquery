import {
  Boxes,
  Columns3,
  GitCompareArrows,
  Network,
  Rows3,
  Table2,
  TextCursorInput,
  type LucideIcon,
} from "lucide-react";

import type { ConceptLevel } from "@/lib/concepts";

import { cn } from "./cn";

/** The catalogue levels, in catalogue order, with their chip label and icon. */
export const LEVELS: Record<ConceptLevel, { label: string; icon: LucideIcon }> = {
  field: { label: "FIELD", icon: TextCursorInput },
  column: { label: "COLUMN", icon: Columns3 },
  pair: { label: "PAIR", icon: GitCompareArrows },
  row: { label: "ROW", icon: Rows3 },
  table: { label: "TABLE", icon: Table2 },
  relationship: { label: "RELATIONSHIP", icon: Network },
  model: { label: "MODEL", icon: Boxes },
};

export const LEVEL_ORDER = Object.keys(LEVELS) as ConceptLevel[];

export function levelChipClass(size: "sm" | "md"): string {
  return cn(
    "inline-flex items-center gap-1 rounded-sm border border-border-strong bg-surface-3 font-mono font-medium tracking-wide text-text-2",
    size === "sm" ? "px-1.5 py-px text-[10px] [&_svg]:size-3" : "px-2 py-0.5 text-[11px] [&_svg]:size-3.5",
  );
}
