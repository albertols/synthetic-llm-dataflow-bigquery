/**
 * The retrieval simulator: the current strategy's seeds on the current
 * space, every strategy measured side by side (coverage, diversity,
 * redundancy), the k-center walk pick by pick, and the seed-candidate ladder.
 */
import { Callout } from "@/components/Callout";
import { InfoHint } from "@/components/InfoHint";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import { formatFixed } from "@/lib/format";

import { spaceColumn, spaceLabel } from "../lib/selection";
import { STRATEGIES } from "../lib/strategies";
import type { RagModel } from "../useRagModel";
import { TOP_K } from "../useRagModel";
import { KcenterWalk } from "./KcenterWalk";
import { SeedLadder } from "./SeedLadder";
import { StrategyMetrics, useStrategyRows } from "./StrategyMetrics";

export function RetrievalSimulator({ model }: { model: RagModel }) {
  const { set, resolved, strategy, k, attempt, setSearch, projection, retrieves, cloud } = model;
  const clusters = projection.pca?.status === "done" ? (projection.pca.clusters ?? null) : null;
  const job = projection.strategies;
  const rows = useStrategyRows(job?.status === "done" ? job.data.rows : null, clusters);
  const walk = job?.status === "done" ? job.data.walk : null;
  if (!set || !resolved) return null;
  const info = STRATEGIES[strategy];
  const current = rows.find((r) => r.id === strategy);
  const pcaResult = projection.pca?.status === "done" ? projection.pca.result : null;

  return (
    <div className="grid gap-5">
      <div className="grid gap-4 rounded-lg border border-border bg-surface-1 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="grid content-start gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-text-1">{info.label}</h3>
            <InfoHint concept={info.concept} />
            {info.inPipeline ? (
              <Badge variant="cpu">exact port of the pipeline</Badge>
            ) : (
              <Badge variant="outline">teaching contrast — not in pipeline</Badge>
            )}
          </div>
          <p className="text-sm text-text-2">Picks {info.picks}.</p>
          <p className="text-xs text-text-3">
            {info.flag ? <span className="font-mono">{info.flag}</span> : null}
            {info.flag ? " · " : ""}
            {info.evidence}
          </p>
          <div className="grid gap-1.5">
            <span className="text-xs font-medium text-text-2">
              Seeds per prompt k = {k}{" "}
              {k === TOP_K ? "(the pipeline's _DEFAULT_TOP_K)" : `(the pipeline uses ${TOP_K})`}
            </span>
            <Slider
              min={1}
              max={16}
              step={1}
              value={[k]}
              thumbLabels={["Seeds per prompt (k)"]}
              formatValue={(v) => `k = ${v}`}
              onValueChange={([v]) => setSearch({ k: v === TOP_K ? undefined : v })}
            />
          </div>
          {strategy === "kcenter_rotate" ? (
            <div className="grid gap-1.5">
              <span className="text-xs font-medium text-text-2">
                Ladder attempt {attempt}: the walk starts at item (attempt × k) mod n ={" "}
                {cloud ? ((attempt * k) % Math.max(cloud.n, 1)) + 1 : "—"}
              </span>
              <Slider
                min={0}
                max={7}
                step={1}
                value={[attempt]}
                thumbLabels={["Ladder attempt"]}
                formatValue={(v) => `attempt ${v}`}
                onValueChange={([v]) => setSearch({ attempt: v || undefined })}
              />
            </div>
          ) : null}
          <Callout tone="info" title="Exact, with one caveat">
            Centroid and k-center run the pipeline&apos;s own arithmetic (pinned by its golden fixtures) over the
            vectors shown. Production asks FAISS in float32, so two scores closer than ~1e-7 can order differently
            there; the k-center walk needs no index. kcenter_rotate&apos;s start depends on item order: here the
            BFF&apos;s, in the pipeline the chunk store&apos;s read (which has no ORDER BY) or the sample&apos;s.
          </Callout>
        </div>
        <div className="grid content-start gap-2">
          <p className="text-xs font-semibold tracking-wide text-text-2 uppercase">
            The {current?.picks.length ?? 0} seeds on {cloud ? spaceLabel(resolved.space).toLowerCase() : "…"}
          </p>
          {!retrieves ? (
            <p className="text-sm text-text-3">
              The pipeline never retrieves over this space. Pick “Row documents” or a column&apos;s values above.
            </p>
          ) : current && cloud ? (
            <ol className="grid gap-1" aria-label="Seeds in pick order" data-testid="seed-list">
              {current.picks.map((i, rank) => (
                <li key={i} className="flex items-baseline gap-2 rounded-sm px-1.5 py-1 text-xs odd:bg-surface-2">
                  <span className="w-5 shrink-0 font-mono text-text-3 tabular-nums">{rank + 1}</span>
                  <span className="line-clamp-2 min-w-0 font-mono text-text-1">{cloud.meta[i]?.chunk_text}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-text-3">Loading the vectors…</p>
          )}
          {resolved.space === "rows" && strategy !== "centroid" ? (
            <p className="text-xs text-text-3">
              On row documents the pipeline only ever asks the centroid (the row exemplars); the seed strategy applies
              to a column&apos;s values — pick one in Space above.
            </p>
          ) : null}
          {current && current.coverage !== null ? (
            <p className="text-xs text-text-3">
              Coverage {formatFixed(current.coverage, 3)} · worst-covered item at {formatFixed(current.radius, 3)} ·
              diversity {formatFixed(current.diversity, 3)} · redundancy {formatFixed(current.redundancy, 3)}
            </p>
          ) : null}
        </div>
      </div>

      {retrieves && rows.length ? (
        <StrategyMetrics rows={rows} current={strategy} onPick={(id) => setSearch({ strategy: id })} />
      ) : null}

      {job?.status === "error" ? (
        <Callout tone="warn" title="The strategies could not be computed">
          {job.message}
        </Callout>
      ) : null}

      {retrieves && cloud && pcaResult && walk ? (
        <KcenterWalk cloud={cloud} coords={pcaResult.coords} strategy={strategy} walk={walk} />
      ) : null}

      <SeedLadder
        key={set.reference_digest + set.embedder_id}
        set={set}
        initialColumn={spaceColumn(resolved.space)}
        strategy={strategy}
        k={k}
        attempt={attempt}
      />
    </div>
  );
}
