/**
 * Where a column's seed candidates come from — the fallback ladder in
 * `_collect_pool_job` / `_column_seed_examples`, in its fixed order:
 *
 *   1. persisted `free_text_col` vectors (distinct values over the sample)
 *   2. the column's distinct values in the first 1,024 rows, embedded locally
 *   3. that column's values in the 8 row exemplars (row-index centroid)
 *   4. the profile's first observed examples
 *
 * and which rung fires depends on which DoFn ladders: the pool branch is
 * never handed the chunk store (rung 2); a Generate engine selects seeds only
 * when it must ladder itself (no pool layer or a pool-store miss: rung 1).
 * Rung 3 cannot fire as wired; rung 4 needs a column with no value in the
 * first 1,024 rows. The candidates and picks are computed here where the
 * browser can (rung 2 needs the embedder: hashing-384 only).
 */
import { Ban, ChevronLeft, ChevronRight, CircleCheck, CornerDownRight, MinusCircle } from "lucide-react";
import { useDeferredValue, useMemo, useState, type ReactNode } from "react";

import type { RagSet } from "@contracts/api";
import { centroidTopK, hashingEmbed } from "@synthetic-platform/stats";

import { InfoHint } from "@/components/InfoHint";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useRagChunks } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatCount } from "@/lib/format";

import { FieldSelect } from "../FieldSelect";
import { distinctColumnValues, learnColumns, parseGreat } from "../lib/greatParse";
import { embedderParam } from "../lib/selection";
import { PIPELINE_STRATEGIES, runStrategy, STRATEGIES, type StrategyId } from "../lib/strategies";
import { rowsOf } from "../lib/vectors";
import { MAX_ROW_DOC_ROWS, MAX_VALUES_PER_COLUMN } from "../useRagModel";

type Branch = "pool" | "generate";
type Store = "hit" | "miss";
type RungState = "fires" | "skipped" | "unreached" | "unreachable";

interface Rung {
  n: 1 | 2 | 3 | 4;
  title: string;
  source: string;
  when: string;
  candidates: number | null;
  seeds: string[] | null;
  note?: string;
}

export function SeedLadder({
  set,
  initialColumn,
  strategy,
  k,
  attempt,
}: {
  set: RagSet;
  initialColumn: string | null;
  strategy: StrategyId;
  k: number;
  attempt: number;
}) {
  const [column, setColumn] = useState<string | null>(initialColumn);
  const [branch, setBranch] = useState<Branch>("pool");
  const [store, setStore] = useState<Store>("miss");
  const [focus, setFocus] = useState<number | null>(null);
  const active = column && set.columns.includes(column) ? column : (set.columns[0] ?? null);
  const pipelineStrategy: StrategyId = (PIPELINE_STRATEGIES as readonly string[]).includes(strategy)
    ? strategy
    : "centroid";

  const base = { digest: set.reference_digest, embedder: embedderParam(set), source_fqn: set.source_fqn };
  const values = useRagChunks(
    active ? { ...base, kind: "free_text_col", column: active, limit: MAX_VALUES_PER_COLUMN } : undefined,
  );
  const rows = useRagChunks({ ...base, kind: "row_doc", limit: MAX_ROW_DOC_ROWS });

  const valuesData = useDeferredValue(values.data);
  const rowsData = useDeferredValue(rows.data);
  const ladder = useMemo((): Rung[] | null => {
    if (!active || !valuesData || !rowsData) return null;
    const v = valuesData.data;
    const r = rowsData.data;
    const texts = r.meta.map((m) => m.chunk_text);
    const columns = learnColumns(texts);
    const pick = (vectors: Float32Array, dim: number, labels: readonly string[]) =>
      labels.length <= k
        ? [...labels]
        : runStrategy(pipelineStrategy, rowsOf(vectors, dim), k, { attempt }).map((i) => labels[i]!);
    const rung1 = pick(
      v.vectors,
      v.dim,
      v.meta.map((m) => m.chunk_text),
    );
    const local = distinctColumnValues(texts, columns, active);
    let rung2: string[] | null = null;
    if (set.embedder_id === "hashing-384" && local.length) {
      const flat = new Float32Array(local.length * 384);
      local.forEach((t, i) => flat.set(hashingEmbed(t), i * 384));
      rung2 = pick(flat, 384, local);
    }
    const exemplarRows = centroidTopK(rowsOf(r.vectors, r.dim), k);
    const rung3 = exemplarRows
      .map((i) => parseGreat(texts[i] ?? "", columns).find((c) => c.column === active)?.value)
      .filter((x): x is string => !!x && x !== "null")
      .slice(0, k);
    return [
      {
        n: 1,
        title: "Persisted value vectors",
        source: `free_text_col chunks of “${active}”: distinct values over the whole sample (≤ ${formatCount(MAX_VALUES_PER_COLUMN)})`,
        when: "A Generate engine that must ladder itself (no pool layer, or a pool-store miss) and finds value chunks.",
        candidates: v.count,
        seeds: v.count ? rung1 : [],
      },
      {
        n: 2,
        title: "Local values, first 1,024 rows",
        source: `the distinct values of “${active}” in the first ${formatCount(MAX_ROW_DOC_ROWS)} reference rows, embedded by the engine`,
        when: "The pool branch — the normal path: it is never handed the chunk store. Also Generate without value chunks.",
        candidates: local.length,
        seeds: rung2,
        note:
          set.embedder_id === "hashing-384"
            ? "Recomputed here with the hashing port, over values parsed from the row documents (in the order the BFF returns them)."
            : "bge cannot run in the browser, so these picks are not computed here.",
      },
      {
        n: 3,
        title: "Row exemplars",
        source: `“${active}” in the ${k} row documents nearest the row centroid (the row index)`,
        when: "Only if rungs 1–2 found nothing — but the same 1,024-row prefix feeds rung 2, so it cannot fire as wired.",
        candidates: rung3.length,
        seeds: rung3,
      },
      {
        n: 4,
        title: "Observed examples",
        source: "the profile's first observed values of the column",
        when: "Only for a column with no value in the first 1,024 rows. The profile is not persisted, so its examples are not shown.",
        candidates: null,
        seeds: null,
      },
    ];
  }, [active, valuesData, rowsData, set.embedder_id, pipelineStrategy, k, attempt]);

  if (!set.columns.length) {
    return (
      <p className="rounded-md border border-dashed border-border px-3 py-4 text-sm text-text-3">
        This table has no free-text value chunks, so no column ladders here. Pick{" "}
        <span className="font-mono">users</span> to follow a column through the ladder.
      </p>
    );
  }

  const fires = !ladder
    ? null
    : branch === "generate" && store === "hit"
      ? 0
      : branch === "generate" && (ladder[0]!.candidates ?? 0) > 0
        ? 1
        : (ladder[1]!.candidates ?? 0) > 0
          ? 2
          : 4;
  const stateOf = (n: number): RungState => {
    if (n === 3) return "unreachable";
    if (fires === null || fires === 0) return "unreached";
    if (n === fires) return "fires";
    if (n < fires) return "skipped";
    return "unreached";
  };
  const current = focus ?? (fires && fires > 0 ? fires : 1);
  const rung = ladder?.[current - 1];

  return (
    <div className="grid gap-4 rounded-lg border border-border bg-surface-1 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-text-1">Where the seed candidates come from</h3>
        <InfoHint concept="rag:seed-ladder" />
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <FieldSelect
          label="Column"
          className="w-44"
          value={active ?? ""}
          options={set.columns.map((c) => ({ value: c, label: c }))}
          onChange={(value) => {
            setColumn(value);
            setFocus(null);
          }}
        />
        <div className="grid gap-1">
          <span className="text-xs font-medium text-text-2" id="ladder-branch">
            Which DoFn ladders
          </span>
          <ToggleGroup
            type="single"
            aria-labelledby="ladder-branch"
            value={branch}
            onValueChange={(value) => {
              if (!value) return;
              setBranch(value as Branch);
              setFocus(null);
            }}
          >
            <ToggleGroupItem value="pool">Pool branch</ToggleGroupItem>
            <ToggleGroupItem value="generate">Generate engine</ToggleGroupItem>
          </ToggleGroup>
        </div>
        {branch === "generate" ? (
          <div className="grid gap-1">
            <span className="text-xs font-medium text-text-2" id="ladder-store">
              Pool store
            </span>
            <ToggleGroup
              type="single"
              aria-labelledby="ladder-store"
              value={store}
              onValueChange={(value) => {
                if (!value) return;
                setStore(value as Store);
                setFocus(null);
              }}
            >
              <ToggleGroupItem value="hit">hit</ToggleGroupItem>
              <ToggleGroupItem value="miss">miss / no pool layer</ToggleGroupItem>
            </ToggleGroup>
          </div>
        ) : null}
      </div>

      {fires === 0 ? (
        <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-sm text-text-2">
          A pool-store hit: Generate reads the persisted pool and never selects a seed. The ladder ran once, in the pool
          branch.
        </p>
      ) : null}

      <ol className="grid gap-2 md:grid-cols-4" aria-label="The four rungs, in the order the code tries them">
        {(ladder ?? placeholderRungs()).map((r) => {
          const state = stateOf(r.n);
          return (
            <li key={r.n}>
              <button
                type="button"
                aria-current={current === r.n ? "step" : undefined}
                onClick={() => setFocus(r.n)}
                className={cn(
                  "grid h-full w-full content-start gap-1.5 rounded-lg border p-3 text-left text-xs transition-colors",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring",
                  current === r.n ? "border-accent bg-accent-soft" : "border-border hover:border-control-border",
                )}
              >
                <span className="flex items-center gap-1.5 text-sm font-medium text-text-1">
                  <span className="font-mono text-text-3">{r.n}</span>
                  {r.title}
                </span>
                <StateLabel state={state} />
                <span className="text-text-3 tabular-nums">
                  {r.candidates === null ? "candidates: not persisted" : `${formatCount(r.candidates)} candidates`}
                </span>
              </button>
            </li>
          );
        })}
      </ol>

      {rung ? (
        <div className="grid gap-2 rounded-md border border-border bg-surface-2 p-3 text-sm" aria-live="polite">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Previous rung"
              disabled={current <= 1}
              onClick={() => setFocus(Math.max(1, current - 1))}
            >
              <ChevronLeft aria-hidden="true" />
            </Button>
            <p className="font-medium text-text-1">
              Rung {rung.n} of 4 · {rung.title}
            </p>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Next rung"
              disabled={current >= 4}
              onClick={() => setFocus(Math.min(4, current + 1))}
            >
              <ChevronRight aria-hidden="true" />
            </Button>
            <StateLabel state={stateOf(rung.n)} />
          </div>
          <Field label="Source">{rung.source}</Field>
          <Field label="Fires when">{rung.when}</Field>
          <Field label={`Seeds (${STRATEGIES[pipelineStrategy].label}, k = ${k})`}>
            {rung.seeds === null ? (
              <span className="text-text-3">{rung.note ?? "Not computable here."}</span>
            ) : rung.seeds.length ? (
              <ul className="flex flex-wrap gap-1.5">
                {rung.seeds.map((s, i) => (
                  <li
                    key={`${s}-${i}`}
                    className="rounded-sm border border-border bg-surface-1 px-1.5 py-0.5 font-mono text-xs text-text-1"
                  >
                    {s}
                  </li>
                ))}
              </ul>
            ) : (
              <span className="text-text-3">No candidates on this rung.</span>
            )}
          </Field>
          {rung.seeds && rung.note ? <p className="text-xs text-text-3">{rung.note}</p> : null}
          {(rung.candidates ?? 0) > 0 && (rung.candidates ?? 0) <= k ? (
            <p className="text-xs text-text-3">
              {rung.candidates} ≤ k = {k}: every candidate is shown, no retrieval runs.
            </p>
          ) : null}
          {strategy !== pipelineStrategy ? (
            <p className="text-xs text-text-3">
              {STRATEGIES[strategy].label} never runs in the pipeline; the picks use centroid, the default.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-3">
      <span className="text-xs font-medium text-text-3">{label}</span>
      <div className="text-xs text-text-2">{children}</div>
    </div>
  );
}

function StateLabel({ state }: { state: RungState }) {
  const map: Record<RungState, { icon: typeof CircleCheck; label: string; variant: "cpu" | "outline" | "neutral" }> = {
    fires: { icon: CircleCheck, label: "fires here", variant: "cpu" },
    skipped: { icon: CornerDownRight, label: "tried, empty", variant: "outline" },
    unreached: { icon: MinusCircle, label: "not reached", variant: "neutral" },
    unreachable: { icon: Ban, label: "cannot fire as wired", variant: "outline" },
  };
  const { icon: Icon, label, variant } = map[state];
  return (
    <Badge variant={variant} className="justify-self-start">
      <Icon aria-hidden="true" />
      {label}
    </Badge>
  );
}

function placeholderRungs(): Rung[] {
  return ([1, 2, 3, 4] as const).map((n) => ({
    n,
    title: ["Persisted value vectors", "Local values, first 1,024 rows", "Row exemplars", "Observed examples"][n - 1]!,
    source: "",
    when: "",
    candidates: null,
    seeds: null,
  }));
}
