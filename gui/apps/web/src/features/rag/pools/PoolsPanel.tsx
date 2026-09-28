/**
 * Free-text pools (`synthetic_rag.freetext_pools`), per (reference digest,
 * LLM weights) and column: the target the code sized (min(num_rows,
 * distinct, 512)), what the ladder delivered, whether it stagnated, the
 * attempts it spent against today's call budget, the route the pool came
 * from (inferred: the row carries none) and how often each value repeats at
 * scale. The table stores attempts and a stagnation flag, not a per-round
 * history, so no round-by-round timeline is drawn.
 */
import { Link } from "@tanstack/react-router";
import type { EChartsOption } from "echarts";
import { ArrowRight, Ban, CircleCheck, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";

import type { FreetextPool } from "@contracts/api";

import { Callout } from "@/components/Callout";
import { ChartFrame } from "@/components/ChartFrame";
import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { StatTile } from "@/components/StatTile";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { usePools } from "@/lib/api";
import { formatCompact, formatCount, formatDate, formatPercent } from "@/lib/format";
import { readToken, useTheme } from "@/lib/theme";

import { FieldSelect } from "../FieldSelect";
import { callBudget, idealRounds, inferPoolRoute, reuseAt, ROUTE_LABELS, type PoolRoute } from "../lib/pools";
import { FREE_TEXT_POOL_MAX, type RagModel } from "../useRagModel";

const ROW_PRESETS = [1_000_000, 10_000_000, 100_000_000];

function modelName(uri: string): string {
  const parts = uri.replace(/\/$/, "").split("/");
  return parts.slice(-3).join("/");
}

export function PoolsPanel({ model }: { model: RagModel }) {
  const { set, facets, search, setSearch, sets } = model;
  const { resolved: theme } = useTheme();
  const poolSets = useMemo(
    () => (facets.data?.data.pools ?? []).filter((p) => set && p.table_fqn === set.source_fqn),
    [facets.data, set],
  );
  const digests = useMemo(() => [...new Set(poolSets.map((p) => p.reference_digest))], [poolSets]);
  const [digestChoice, setDigestChoice] = useState<string | null>(null);
  const digest =
    digestChoice && digests.includes(digestChoice)
      ? digestChoice
      : set && digests.includes(set.reference_digest)
        ? set.reference_digest
        : (digests[0] ?? null);
  const pools = usePools(digest ? { digest } : undefined);
  const models = useMemo(() => [...new Set((pools.data?.data ?? []).map((p) => p.model_uri))].sort(), [pools.data]);
  const modelUri = search.model && models.includes(search.model) ? search.model : (models[0] ?? null);
  const numRowsOptions = useMemo(
    () => [...new Set([...(facets.data?.data.num_rows ?? []), ...ROW_PRESETS])].sort((a, b) => a - b),
    [facets.data],
  );
  const [rows, setRows] = useState<number | null>(null);
  const atRows = rows ?? (numRowsOptions.includes(10_000_000) ? 10_000_000 : (numRowsOptions[0] ?? 10_000_000));

  const columns = useMemo(() => {
    const fromSets = sets.filter((s) => set && s.source_fqn === set.source_fqn).flatMap((s) => s.columns);
    const fromPools = (pools.data?.data ?? []).map((p) => p.column);
    return [...new Set([...fromSets, ...fromPools])];
  }, [sets, set, pools.data]);

  const table = useMemo(() => {
    const byColumn = new Map<string, FreetextPool>();
    for (const p of pools.data?.data ?? []) if (p.model_uri === modelUri) byColumn.set(p.column, p);
    return columns.map((column) => {
      const pool = byColumn.get(column) ?? null;
      const route = inferPoolRoute(pool);
      const budget = pool ? callBudget(pool.target) : null;
      return {
        column,
        pool,
        route,
        target: pool?.target ?? null,
        size: pool?.distinct ?? null,
        fill: pool && pool.target > 0 ? pool.distinct / pool.target : null,
        stagnated: pool?.stagnated ?? null,
        attempts: pool?.attempts ?? null,
        budget,
        ideal: pool ? idealRounds(pool.target) : null,
        reuse: pool ? reuseAt(atRows, pool.distinct) : null,
      };
    });
  }, [pools.data, modelUri, columns, atRows]);
  const withPools = table.filter((r) => r.pool);

  const fillOption = useMemo((): EChartsOption => {
    void theme;
    const ink = readToken("--text-1", "#f2f4f7");
    return {
      grid: { left: 92, right: 48, top: 28, bottom: 28 },
      legend: { top: 0, left: 0, data: ["pool size", "target"] },
      xAxis: {
        type: "value",
        min: 0,
        max: Math.max(FREE_TEXT_POOL_MAX, ...withPools.map((r) => r.target ?? 0)),
        axisLabel: { hideOverlap: true },
      },
      yAxis: { type: "category", inverse: true },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      series: [
        {
          name: "pool size",
          type: "bar",
          encode: { y: "column", x: "size" },
          barMaxWidth: 16,
          itemStyle: { borderRadius: [0, 4, 4, 0] },
          label: {
            show: true,
            position: "right",
            formatter: (p: { value: unknown }) => {
              const row = p.value as { stagnated?: string };
              return row.stagnated === "yes" ? "stagnated" : "";
            },
          },
        },
        {
          name: "target",
          type: "scatter",
          encode: { y: "column", x: "target" },
          symbol: "rect",
          symbolSize: [3, 20],
          itemStyle: { color: ink },
        },
      ],
    };
  }, [theme, withPools]);

  const attemptsOption = useMemo((): EChartsOption => {
    void theme;
    const ink = readToken("--text-1", "#f2f4f7");
    return {
      grid: { left: 92, right: 24, top: 28, bottom: 28 },
      legend: { top: 0, left: 0, data: ["attempts", "call budget today"] },
      xAxis: { type: "value", min: 0, minInterval: 1, axisLabel: { hideOverlap: true } },
      yAxis: { type: "category", inverse: true },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      series: [
        {
          name: "attempts",
          type: "bar",
          encode: { y: "column", x: "attempts" },
          barMaxWidth: 16,
          itemStyle: { borderRadius: [0, 4, 4, 0] },
        },
        {
          name: "call budget today",
          type: "scatter",
          encode: { y: "column", x: "budget" },
          symbol: "rect",
          symbolSize: [3, 20],
          itemStyle: { color: ink },
        },
      ],
    };
  }, [theme]);

  const chartRows = useMemo(
    () =>
      withPools.map((r) => ({
        column: r.column,
        size: r.size,
        target: r.target,
        stagnated: r.stagnated ? "yes" : "no",
        attempts: r.attempts,
        budget: r.budget,
      })),
    [withPools],
  );

  if (!set) return null;
  if (!digests.length) {
    return (
      <EmptyState
        headingLevel={3}
        title={`No free-text pools for ${set.source_fqn.split(".").pop()}`}
        description="Pools exist only where the pool layer ran (--build_pool_layer) for a table with LLM-routed free-text columns. Pick users to see some."
      />
    );
  }
  const headline = withPools.find((r) => r.route === "llm_ladder") ?? withPools[0];
  const overBudget = withPools.filter((r) => r.budget !== null && (r.attempts ?? 0) > r.budget);

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <FieldSelect
          label="Reference sample"
          className="w-60"
          value={digest ?? ""}
          options={digests.map((d) => ({
            value: d,
            label: d.slice(0, 10),
            hint: d === set.reference_digest ? "the sample above" : "another sample",
          }))}
          onChange={setDigestChoice}
        />
        <FieldSelect
          label="LLM weights (model_uri)"
          className="w-72"
          value={modelUri ?? ""}
          options={models.map((m) => ({ value: m, label: modelName(m) }))}
          onChange={(value) => setSearch({ model: value })}
        />
        <FieldSelect
          label="Rows generated"
          className="w-44"
          hint={<InfoHint concept="rag:pool-reuse" />}
          value={String(atRows)}
          options={numRowsOptions.map((n) => ({ value: String(n), label: formatCount(n) }))}
          onChange={(value) => setRows(Number(value))}
        />
        <Link
          to="/config"
          search={{ knob: "free_text_pool_max" }}
          className="inline-flex h-9 items-center gap-1 rounded-md px-2 text-sm text-link hover:underline focus-visible:outline-2 focus-visible:outline-focus-ring"
        >
          Tune the pool cap in CONFIG
          <ArrowRight className="size-3.5" aria-hidden="true" />
        </Link>
      </div>

      {pools.isPending ? (
        <Skeleton className="h-48 w-full" />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <StatTile
              label="Pool cap"
              concept="rag:pool-target"
              value={FREE_TEXT_POOL_MAX}
              format={(v) => formatCount(v)}
              footnote="target = min(num_rows, distinct, 512)"
            />
            <StatTile
              label={headline ? `Each ${headline.column} value repeats` : "Reuse at scale"}
              concept="rag:pool-reuse"
              value={headline?.reuse ?? null}
              format={(v) => `${formatCompact(v)}×`}
              footnote={
                headline ? `${formatCount(atRows)} rows ÷ ${formatCount(headline.size)} pooled values` : undefined
              }
            />
            <StatTile
              label="Columns that stagnated"
              concept="rag:pool-stagnation"
              value={withPools.filter((r) => r.stagnated).length}
              format={(v) => `${v} of ${withPools.length}`}
              footnote="the ladder stopped yielding novel values before its target"
            />
          </div>

          <div
            className="relative min-w-0 overflow-x-auto rounded-lg border border-border"
            role="region"
            aria-label="Pools per column"
            tabIndex={0}
          >
            <table className="w-full min-w-[52rem] text-left text-xs">
              <caption className="sr-only">
                Free-text pools of {modelUri ? modelName(modelUri) : "—"} for reference sample {digest?.slice(0, 10)}
              </caption>
              <thead className="bg-surface-2 text-text-3">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Column
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    <span className="inline-flex items-center gap-1">
                      Route <InfoHint concept="rag:pool-routes" />
                    </span>
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Target
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Pool size
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Fill
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Ladder exit
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    <span className="inline-flex items-center gap-1">
                      Attempts / budget <InfoHint concept="rag:call-budget" />
                    </span>
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Repeats at {formatCompact(atRows)} rows
                  </th>
                </tr>
              </thead>
              <tbody>
                {table.map((r) => (
                  <tr key={r.column} className="border-t border-border align-top">
                    <th scope="row" className="px-3 py-2 font-mono font-medium text-text-1">
                      {r.column}
                    </th>
                    <td className="px-3 py-2">
                      <RouteBadge route={r.route} />
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-text-1 tabular-nums">{formatCount(r.target)}</td>
                    <td className="px-3 py-2 text-right font-mono text-text-1 tabular-nums">{formatCount(r.size)}</td>
                    <td className="px-3 py-2 text-right font-mono text-text-2 tabular-nums">
                      {r.fill === null ? "—" : formatPercent(r.fill, 0)}
                    </td>
                    <td className="px-3 py-2">
                      {r.pool ? (
                        r.stagnated ? (
                          <span className="inline-flex items-center gap-1 text-status-warn-text">
                            <TriangleAlert className="size-3.5" aria-hidden="true" />
                            stagnated
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-text-2">
                            <CircleCheck className="size-3.5" aria-hidden="true" />
                            {r.fill !== null && r.fill >= 1 ? "reached target" : "no stagnation flag"}
                          </span>
                        )
                      ) : (
                        <span className="text-text-3">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums">
                      {r.pool ? (
                        <span
                          className={
                            r.budget !== null && (r.attempts ?? 0) > r.budget
                              ? "inline-flex items-center gap-1 text-status-warn-text"
                              : "text-text-1"
                          }
                        >
                          {r.budget !== null && (r.attempts ?? 0) > r.budget ? (
                            <>
                              <TriangleAlert className="size-3.5" aria-hidden="true" />
                              <span className="sr-only">Above today&apos;s budget:</span>
                            </>
                          ) : null}
                          {r.attempts} / {r.budget}
                          <span className="text-text-3"> (≥ {r.ideal} ideal)</span>
                        </span>
                      ) : (
                        <span className="text-text-3">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-text-1 tabular-nums">
                      {r.reuse === null ? "—" : `${formatCompact(r.reuse)}×`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {overBudget.length ? (
            <Callout tone="warn" title="More attempts than today's budget">
              {overBudget.map((r) => r.column).join(", ")}: the row records more ladder calls than today&apos;s cap of
              max(3, 2·⌈target / (32·4)⌉). A row written before the n = 4 parallel completions (2026-07-25) had a budget
              of 2·⌈target / 32⌉.{pools.data?.dataSource === "mock" ? " In mock mode the attempts are invented." : ""}
            </Callout>
          ) : null}

          <div className="grid gap-4 lg:grid-cols-2">
            <ChartFrame
              title="Pool size against its target"
              concept="rag:pool-target"
              description="Bars: distinct values pooled · marks: the target the code sized"
              option={fillOption}
              data={chartRows}
              height={Math.max(160, 44 * chartRows.length + 60)}
              empty={{ when: !chartRows.length, message: "No pool rows for this model." }}
              columns={[
                { key: "column", label: "Column" },
                { key: "size", label: "Pool size", align: "right" },
                { key: "target", label: "Target", align: "right" },
                { key: "stagnated", label: "Stagnated" },
              ]}
            />
            <ChartFrame
              title="Ladder attempts against the call budget"
              concept="rag:call-budget"
              description="Bars: attempts recorded · marks: max(3, 2·⌈target / 128⌉) today"
              option={attemptsOption}
              data={chartRows}
              height={Math.max(160, 44 * chartRows.length + 60)}
              empty={{ when: !chartRows.length, message: "No pool rows for this model." }}
              columns={[
                { key: "column", label: "Column" },
                { key: "attempts", label: "Attempts", align: "right" },
                { key: "budget", label: "Budget today", align: "right" },
              ]}
            />
          </div>

          {withPools.length ? <PoolPreview pools={withPools.map((r) => r.pool!)} /> : null}
          <p className="text-xs text-text-3">
            A pool row keeps its target, values, stagnation flag and attempt count — not a round-by-round history — so
            the ladder is summarised, not replayed. Pools are keyed by (reference digest, model_uri): a pool built for a
            small run serves a large one{digest ? ` · digest ${digest.slice(0, 10)}` : ""}
            {set.created_at ? ` · RAG set of ${formatDate(set.created_at)}` : ""}.
          </p>
        </>
      )}
    </div>
  );
}

const ROUTE_ICON: Record<PoolRoute, typeof CircleCheck> = {
  llm_ladder: CircleCheck,
  binary_fallback: Ban,
  no_rounds: TriangleAlert,
  no_pool: ArrowRight,
};

/** The pool's route, linking to the knobs that shape free-text pools: CONFIG's FREE TEXT channel. */
function RouteBadge({ route }: { route: PoolRoute }) {
  const Icon = ROUTE_ICON[route];
  return (
    <Link
      to="/config"
      search={{ section: "amp", channel: "free_text" }}
      title="The FREE TEXT knobs in Config"
      className="inline-flex rounded-full focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
    >
      <Badge
        variant={route === "llm_ladder" ? "gpu" : route === "no_pool" ? "outline" : "neutral"}
        className="hover:brightness-125"
      >
        <Icon aria-hidden="true" />
        {ROUTE_LABELS[route]}
      </Badge>
      <span className="sr-only">: the FREE TEXT knobs in Config</span>
    </Link>
  );
}

function PoolPreview({ pools }: { pools: readonly FreetextPool[] }) {
  const [column, setColumn] = useState(pools[0]!.column);
  const pool = pools.find((p) => p.column === column) ?? pools[0]!;
  return (
    <div className="grid gap-2 rounded-lg border border-border bg-surface-1 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <FieldSelect
          label="Pool values"
          className="w-44"
          value={pool.column}
          options={pools.map((p) => ({ value: p.column, label: p.column }))}
          onChange={setColumn}
        />
        <p className="text-xs text-text-3">
          The first 24 of {formatCount(pool.distinct)} in insertion order. Rows draw from them uniformly, with
          replacement.
        </p>
      </div>
      <ul className="flex flex-wrap gap-1.5" aria-label={`First values of the ${pool.column} pool`}>
        {pool.values.slice(0, 24).map((v, i) => (
          <li
            key={`${v}-${i}`}
            className="rounded-sm border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-text-1"
          >
            {v}
          </li>
        ))}
      </ul>
    </div>
  );
}
