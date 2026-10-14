/**
 * What a selection holds (a lasso, or an isolated legend category — the
 * keyboard path): its size, its mean pairwise cosine in 384-d next to the
 * whole cloud's, and which kinds and columns it spans.
 */
import { X } from "lucide-react";
import { useMemo } from "react";

import { InfoHint } from "@/components/InfoHint";
import { Button } from "@/components/ui/button";
import { formatCount, formatFixed, formatPercent } from "@/lib/format";

import { kindLabel } from "../lib/categories";
import { meanPairwiseCosine } from "../lib/metrics";
import type { Cloud } from "../lib/useCloud";

export function SelectionStats({
  cloud,
  indices,
  source,
  baseline,
  onClear,
}: {
  cloud: Cloud;
  indices: readonly number[];
  /** "lasso" or the isolated category's label. */
  source: string;
  /** Mean pairwise cosine of the whole cloud (sampled). */
  baseline: number | null;
  onClear: () => void;
}) {
  const stats = useMemo(() => meanPairwiseCosine(cloud.rows, indices, cloud.norms, 40_000), [cloud, indices]);
  const spans = useMemo(() => {
    const counts = new Map<string, number>();
    for (const i of indices) {
      const m = cloud.meta[i]!;
      const key =
        m.chunk_kind === "row_doc" ? `${kindLabel(m.chunk_kind)} · ${cloud.tables[i]}` : (m.column ?? "value");
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [cloud, indices]);
  return (
    <div className="grid gap-2 rounded-md border border-border bg-surface-2 px-3 py-3" aria-live="polite">
      <div className="flex items-center gap-1">
        <p className="text-sm font-medium text-text-1">
          Selection ({source}): {formatCount(indices.length)} of {formatCount(cloud.n)} points
        </p>
        <InfoHint concept="rag:selection-cosine" />
        <Button size="icon-sm" variant="ghost" className="ml-auto" aria-label="Clear the selection" onClick={onClear}>
          <X aria-hidden="true" />
        </Button>
      </div>
      {indices.length < 2 ? (
        <p className="text-xs text-text-3">Select at least two points to compare them.</p>
      ) : (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          <dt className="text-text-3">Mean cosine inside</dt>
          <dd className="font-mono text-text-1 tabular-nums">
            {stats ? formatFixed(stats.mean, 3) : "—"}
            {stats && !stats.exact ? <span className="text-text-3"> (≈, {formatCount(stats.pairs)} pairs)</span> : null}
          </dd>
          <dt className="text-text-3">Whole cloud</dt>
          <dd className="font-mono text-text-2 tabular-nums">{baseline === null ? "—" : formatFixed(baseline, 3)}</dd>
          <dt className="text-text-3">Share of the cloud</dt>
          <dd className="font-mono text-text-2 tabular-nums">{formatPercent(indices.length / Math.max(cloud.n, 1))}</dd>
        </dl>
      )}
      {spans.length ? (
        <ul className="flex flex-wrap gap-1.5 text-xs" aria-label="What the selection spans">
          {spans.slice(0, 8).map(([label, count]) => (
            <li key={label} className="rounded-full border border-border px-2 py-0.5 text-text-2">
              {label} <span className="text-text-3 tabular-nums">{formatCount(count)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
