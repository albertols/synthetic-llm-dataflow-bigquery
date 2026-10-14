import type { ConceptLevel } from "@/lib/concepts";
import { cn } from "@/lib/cn";
import { LEVELS, levelChipClass } from "@/lib/levels";

import { InfoHint } from "./InfoHint";

export { LEVELS, LEVEL_ORDER } from "@/lib/levels";

export type LevelChipProps = {
  level: ConceptLevel;
  size?: "sm" | "md";
  /** Adds the level's (i) InfoHint ("core:level-<level>"). */
  withHint?: boolean;
  className?: string;
};

/** The catalogue level of a metric: icon + upper-case label (never colour alone). */
export function LevelChip({ level, size = "md", withHint = false, className }: LevelChipProps) {
  const { label, icon: Icon } = LEVELS[level];
  return (
    <span className={cn("inline-flex items-center gap-0.5", className)}>
      <span data-level={level} className={levelChipClass(size)}>
        <Icon aria-hidden="true" />
        {label}
      </span>
      {withHint ? <InfoHint concept={`core:level-${level}`} size="sm" /> : null}
    </span>
  );
}
