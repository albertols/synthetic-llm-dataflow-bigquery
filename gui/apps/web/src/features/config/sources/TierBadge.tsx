import type { SourceStatsSnapshot } from "@contracts/api";

import { Badge } from "@/components/ui/badge";

/** Sample vs exact tier, and the legacy NULL tier (read as sample), always in words. */
export function TierBadge({ snapshot }: { snapshot: Pick<SourceStatsSnapshot, "tier" | "legacy_tier"> }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <Badge variant={snapshot.tier === "exact" ? "cpu" : "info"}>{snapshot.tier} tier</Badge>
      {snapshot.legacy_tier ? <Badge variant="outline">NULL tier → sample</Badge> : null}
    </span>
  );
}
