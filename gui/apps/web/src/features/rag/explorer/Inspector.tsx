/**
 * The selected chunk (click a point, a query result or a neighbour): its
 * kind, column and exact text, its norm, and its five nearest neighbours by
 * cosine in the full space — each one a button, so the cloud can be walked
 * from the keyboard.
 */
import { MousePointerClick, X } from "lucide-react";

import { InfoHint } from "@/components/InfoHint";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatFixed } from "@/lib/format";

import { kindLabel } from "../lib/categories";
import type { Cloud } from "../lib/useCloud";
import type { Hit } from "../lib/vectors";

export function Inspector({
  cloud,
  index,
  neighbours,
  seedRank,
  onSelect,
  noise,
}: {
  cloud: Cloud;
  index: number | null;
  neighbours: readonly Hit[];
  /** 1-based position among the current seeds, if it is one. */
  seedRank: number | null;
  onSelect: (index: number | null) => void;
  /** ±1/√d band for a lexical embedder, else null. */
  noise: number | null;
}) {
  if (index === null || !cloud.meta[index]) {
    return (
      <div className="flex items-start gap-2 rounded-md border border-dashed border-border px-3 py-3 text-sm text-text-3">
        <MousePointerClick className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <p>
          Hover a point to read its text; click it (or pick a query result below) to pin it here with its nearest
          neighbours.
        </p>
      </div>
    );
  }
  const meta = cloud.meta[index];
  return (
    <div className="grid gap-3" data-testid="inspector">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="outline">{kindLabel(meta.chunk_kind)}</Badge>
        {meta.column ? <Badge variant="outline">{meta.column}</Badge> : null}
        <Badge variant="outline">{cloud.tables[index]}</Badge>
        {seedRank ? <Badge variant="accent">seed #{seedRank}</Badge> : null}
        <span className="text-xs text-text-3 tabular-nums">point {index + 1}</span>
        <Button
          size="icon-sm"
          variant="ghost"
          className="ml-auto"
          aria-label="Clear the selected point"
          onClick={() => onSelect(null)}
        >
          <X aria-hidden="true" />
        </Button>
      </div>
      <div
        role="region"
        aria-label="Chunk text"
        tabIndex={0}
        className="max-h-32 overflow-y-auto rounded-md bg-surface-2 px-3 py-2 font-mono text-xs leading-relaxed break-words text-text-1 focus-visible:outline-2 focus-visible:outline-focus-ring"
      >
        {meta.chunk_text}
      </div>
      <p className="text-xs text-text-3">
        ‖v‖ = {formatFixed(cloud.norms[index], 4)} · {cloud.dim} dims · row digest{" "}
        <span className="font-mono">{meta.row_digest.slice(0, 12)}…</span>
      </p>
      <div className="grid gap-1.5">
        <div className="flex items-center gap-1 text-xs font-semibold tracking-wide text-text-2 uppercase">
          Nearest by cosine (384-d)
          <InfoHint concept="rag:cosine" />
        </div>
        <ol className="grid gap-1">
          {neighbours.map((hit) => (
            <li key={hit.index}>
              <button
                type="button"
                onClick={() => onSelect(hit.index)}
                className="flex w-full min-h-7 items-baseline gap-2 rounded-sm px-1.5 py-1 text-left text-xs hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-focus-ring"
              >
                <span className="w-12 shrink-0 font-mono text-text-1 tabular-nums">{formatFixed(hit.score, 3)}</span>
                <span className="line-clamp-1 min-w-0 text-text-2">{cloud.meta[hit.index]?.chunk_text}</span>
              </button>
            </li>
          ))}
        </ol>
        {noise !== null ? (
          <p className="text-xs text-text-3">
            With the hashing embedder, cosines within ±{formatFixed(noise, 3)} of 0 are collision noise, not similarity.
          </p>
        ) : null}
      </div>
    </div>
  );
}
