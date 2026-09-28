/**
 * /evaluation — every evaluation (the `evaluation_latest` semantics: one row
 * per evaluation, its latest registry event), filtered by every facet in the
 * URL, with preset and saved views, a score history, and row picks for the
 * compare view.
 */
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import type { EChartsOption } from "echarts";
import {
  ArrowDown,
  ArrowUp,
  Bookmark,
  ChevronLeft,
  ChevronRight,
  GitCompareArrows,
  Link2,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

import type { EvaluationSummary, Facets } from "@contracts/api";

import { Callout } from "@/components/Callout";
import { ChartFrame } from "@/components/ChartFrame";
import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { PageHeader } from "@/components/PageHeader";
import { StatTile } from "@/components/StatTile";
import { StatusPill } from "@/components/StatusPill";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "@/components/ui/toast";
import { useEvaluations, useFacets } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatCompact, formatCount, formatDateTime, MISSING } from "@/lib/format";

import { metricMeta } from "./lib/catalogue";
import { slotMap } from "./lib/compare";
import {
  activeChips,
  EMPTY_FILTERS,
  FACETS,
  PAGE_SIZE,
  presetViews,
  readSavedViews,
  toFilter,
  writeSavedViews,
  type FacetDef,
  type SavedView,
} from "./lib/filters";
import { fmtScore, fmtSig, shortModel } from "./lib/format";
import { useChartTokens } from "./lib/tokens";
import type { EvaluationListSearch } from "./route";

const route = getRouteApi("/evaluation");

const ROLLUP = metricMeta("model.overall_score")?.thresholds ?? { warn: 0.85, fail: 0.7 };

/** The status the catalogue's roll-up thresholds give a family score (inclusive; higher is better). */
export function scoreStatus(score: number | null): string | null {
  if (score === null) return null;
  if (ROLLUP.fail !== null && score <= ROLLUP.fail) return "fail";
  if (ROLLUP.warn !== null && score <= ROLLUP.warn) return "warn";
  return "pass";
}

const GLYPH: Record<string, string> = { pass: "✓", warn: "!", fail: "✕" };

function ScoreCell({ score, label }: { score: number | null; label: string }) {
  const status = scoreStatus(score);
  return (
    <span
      className="inline-flex items-center justify-end gap-1.5 tabular-nums"
      title={status ? `${label}: ${fmtScore(score)} (${status} at the roll-up thresholds)` : undefined}
    >
      <span
        aria-hidden="true"
        className="relative hidden h-1 w-8 overflow-hidden rounded-full bg-surface-3 xl:inline-block"
      >
        {score !== null ? (
          <span
            className="absolute inset-y-0 left-0 bg-seq-4"
            style={{ width: `${Math.max(0, Math.min(1, score)) * 100}%` }}
          />
        ) : null}
      </span>
      <span
        className={cn(
          status === "fail" && "font-semibold text-status-critical-text",
          status === "warn" && "text-status-warn-text",
        )}
      >
        {fmtScore(score)}
      </span>
      <span aria-hidden="true" className="w-2 text-[10px] text-text-3">
        {status ? GLYPH[status] : ""}
      </span>
      {status && status !== "pass" ? <span className="sr-only">({status})</span> : null}
    </span>
  );
}

function FacetBox({
  def,
  facets,
  value,
  onChange,
}: {
  def: FacetDef;
  facets: Facets;
  value: (string | number)[] | undefined;
  onChange: (next: (string | number)[] | undefined) => void;
}) {
  const options = def.options(facets);
  return (
    <Combobox
      label={def.label}
      multiple
      options={options}
      value={(value ?? []).map(String)}
      onValueChange={(next) => onChange(next.length ? (def.numeric ? next.map(Number) : next) : undefined)}
      placeholder={def.label}
      className="w-[calc(50%-0.25rem)] min-w-0 sm:w-44"
    />
  );
}

/** The id search: typed locally, committed to the URL (and so to the API) 400 ms after the last keystroke or on Enter. */
function DebouncedSearch({ value, onCommit }: { value: string; onCommit: (value: string) => void }) {
  const [draft, setDraft] = useState(value);
  const [synced, setSynced] = useState(value);
  if (value !== synced) {
    // The URL changed from elsewhere (a chip removed, a view applied): follow it.
    setSynced(value);
    setDraft(value);
  }
  useEffect(() => {
    if (draft === value) return undefined;
    const timer = globalThis.setTimeout(() => onCommit(draft.trim()), 400);
    return () => globalThis.clearTimeout(timer);
  }, [draft, value, onCommit]);
  return (
    <label className="flex w-full items-center sm:w-56">
      <span className="sr-only">Search evaluation, run or job id</span>
      <input
        type="search"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => event.key === "Enter" && onCommit(draft.trim())}
        placeholder="Evaluation, run or job id…"
        className="h-9 w-full rounded-md border border-control-border bg-surface-1 px-3 text-sm text-text-1 placeholder:text-text-3"
      />
    </label>
  );
}

function ScoreHistory({ search }: { search: EvaluationListSearch }) {
  const tokens = useChartTokens();
  const navigate = useNavigate();
  const history = useEvaluations({
    ...toFilter(search, 200),
    offset: 0,
    limit: 200,
    sort: "evaluated_at",
    order: "desc",
  });
  const items = history.data?.data.items;
  const spec = useMemo(() => {
    const points = (items ?? []).filter((e) => e.overall_score !== null);
    if (!points.length) return null;
    const engines = slotMap([...new Set(points.map((p) => p.engine))].sort());
    const series: NonNullable<EChartsOption["series"]> = [...engines.entries()].map(([engine, slot]) => ({
      type: "scatter",
      name: engine,
      data: points
        .filter((p) => (p.engine ?? "—") === engine)
        .map((p) => [Date.parse(p.evaluated_at), Number(p.overall_score!.toFixed(4)), p.evaluation_id]),
      symbolSize: 9,
      itemStyle: { color: slot ? tokens.slots[slot - 1] : tokens.other, borderColor: tokens.surface, borderWidth: 2 },
      cursor: "pointer",
    }));
    const lines = {
      type: "line" as const,
      name: "thresholds",
      data: [],
      silent: true,
      markLine: {
        symbol: "none",
        silent: true,
        data: [
          {
            yAxis: ROLLUP.warn ?? 0.85,
            lineStyle: { color: tokens.warn, width: 1, type: "solid" as const },
            label: { formatter: `warn ${ROLLUP.warn}`, color: tokens.text2, position: "insideEndTop" as const },
          },
          {
            yAxis: ROLLUP.fail ?? 0.7,
            lineStyle: { color: tokens.critical, width: 1, type: "solid" as const },
            label: { formatter: `fail ${ROLLUP.fail}`, color: tokens.text2, position: "insideEndTop" as const },
          },
        ],
      },
    };
    const minScore = Math.min(...points.map((p) => p.overall_score!));
    const option: EChartsOption = {
      dataset: [],
      legend: { top: 0, left: 0, data: [...engines.keys()] },
      grid: { left: 8, right: 24, top: 36, bottom: 8, containLabel: true },
      tooltip: {
        trigger: "item",
        formatter: (params: unknown) => {
          const { data, seriesName } = params as { data?: [number, number, string]; seriesName?: string };
          return data
            ? `${data[2]} · ${seriesName ?? ""}<br/>${formatDateTime(data[0])}<br/>overall ${data[1].toFixed(3)} — click to open`
            : "";
        },
      },
      xAxis: { type: "time", axisLabel: { hideOverlap: true } },
      yAxis: {
        type: "value",
        min: Number(Math.max(0, Math.floor(Math.min(minScore, ROLLUP.fail ?? 0.7) * 20) / 20 - 0.05).toFixed(2)),
        max: 1,
      },
      series: [...series, lines],
    };
    const data = points.map((p) => ({
      evaluation: p.evaluation_id,
      evaluated_at: formatDateTime(p.evaluated_at),
      engine: p.engine,
      overall_score: p.overall_score,
      status: p.status,
    }));
    return { option, data };
  }, [items, tokens]);
  const onEvents = useMemo(
    () => ({
      click: (params: unknown) => {
        const id = (params as { data?: [number, number, string] }).data?.[2];
        if (id) void navigate({ to: "/evaluation/$evaluationId", params: { evaluationId: id } });
      },
    }),
    [navigate],
  );
  if (history.isPending) return <Skeleton className="h-64 w-full" />;
  return (
    <ChartFrame
      title="Overall score over time"
      concept="core:score"
      description={`The ${formatCount(items?.length ?? 0)} newest evaluations under these filters, by engine. Lines: the roll-up warn and fail thresholds. Click a point to open its run.`}
      option={spec?.option ?? {}}
      data={spec?.data ?? []}
      empty={{ when: !spec, message: "No scored evaluation under these filters." }}
      onEvents={onEvents}
      height={240}
    />
  );
}

const SORTABLE: Record<string, NonNullable<EvaluationListSearch["sort"]>> = {
  evaluated: "evaluated_at",
  status: "status",
  overall: "overall_score",
  fidelity: "fidelity_score",
  privacy: "privacy_score",
  integrity: "integrity_score",
  diversity: "diversity_score",
  rows: "num_rows_requested",
};

function SortHead({
  id,
  label,
  search,
  onSort,
  align,
}: {
  id: keyof typeof SORTABLE;
  label: string;
  search: EvaluationListSearch;
  onSort: (sort: NonNullable<EvaluationListSearch["sort"]>) => void;
  align?: "right";
}) {
  const key = SORTABLE[id]!;
  const active = (search.sort ?? "evaluated_at") === key;
  const order = search.order ?? "desc";
  return (
    <TableHead
      scope="col"
      className={cn(align === "right" && "text-right")}
      aria-sort={active ? (order === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        onClick={() => onSort(key)}
        className="inline-flex cursor-pointer items-center gap-1 hover:text-text-1"
      >
        {label}
        {active ? (
          order === "asc" ? (
            <ArrowUp className="size-3" aria-hidden="true" />
          ) : (
            <ArrowDown className="size-3" aria-hidden="true" />
          )
        ) : null}
      </button>
    </TableHead>
  );
}

/**
 * The list's "fail · warn · n/e" cell. Its screen-reader text is the full
 * breakdown, and the registry row has no info/other column, so "info or other"
 * is the remainder: metrics_total minus the counted statuses (list-page.test).
 */
export function MetricsCell({ e }: { e: EvaluationSummary }) {
  if (e.metrics_total === null) return <span className="text-text-3">{MISSING}</span>;
  const gated = (e.metrics_pass ?? 0) + (e.metrics_warn ?? 0) + (e.metrics_fail ?? 0) + (e.metrics_not_evaluated ?? 0);
  const rest = e.metrics_total - gated;
  const full = `${e.metrics_fail ?? 0} fail · ${e.metrics_warn ?? 0} warn · ${e.metrics_pass ?? 0} pass${rest > 0 ? ` · ${rest} info or other` : ""}${e.metrics_not_evaluated ? ` · ${e.metrics_not_evaluated} not evaluated` : ""} = ${e.metrics_total} metrics`;
  return (
    <span className="text-xs whitespace-nowrap tabular-nums" title={full}>
      <span className="sr-only">{full}</span>
      <span aria-hidden="true">
        <span className={cn(e.metrics_fail ? "font-semibold text-status-critical-text" : "text-text-2")}>
          {e.metrics_fail ?? 0} fail
        </span>
        <span className="text-text-3"> · </span>
        <span className="text-text-2">{e.metrics_warn ?? 0} warn</span>
        {e.metrics_not_evaluated ? <span className="text-text-3"> · {e.metrics_not_evaluated} n/e</span> : null}
      </span>
    </span>
  );
}

function EvaluationsTable({
  items,
  search,
  picked,
  onPick,
  onSort,
}: {
  items: EvaluationSummary[];
  search: EvaluationListSearch;
  picked: Set<string>;
  onPick: (id: string) => void;
  onSort: (sort: NonNullable<EvaluationListSearch["sort"]>) => void;
}) {
  return (
    <TableContainer aria-label="Evaluations" className="max-h-[75vh]">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead scope="col" className="sticky left-0 z-20 w-10 bg-surface-2">
              <span className="sr-only">Pick for compare</span>
            </TableHead>
            <TableHead scope="col" className="sticky left-10 z-20 bg-surface-2">
              Evaluation
            </TableHead>
            <SortHead id="evaluated" label="Evaluated" search={search} onSort={onSort} />
            <SortHead id="status" label="Status" search={search} onSort={onSort} />
            <SortHead id="overall" label="Overall" search={search} onSort={onSort} align="right" />
            <SortHead id="fidelity" label="Fidelity" search={search} onSort={onSort} align="right" />
            <SortHead id="privacy" label="Privacy" search={search} onSort={onSort} align="right" />
            <SortHead id="integrity" label="Integrity" search={search} onSort={onSort} align="right" />
            <SortHead id="diversity" label="Diversity" search={search} onSort={onSort} align="right" />
            <TableHead scope="col">Metrics</TableHead>
            <TableHead scope="col">Engine · model</TableHead>
            <TableHead scope="col">Embedder · retrieval</TableHead>
            <TableHead scope="col" className="text-right">
              Similarity
            </TableHead>
            <TableHead scope="col">Seed</TableHead>
            <TableHead scope="col" className="text-right">
              Ref rows
            </TableHead>
            <SortHead id="rows" label="Rows" search={search} onSort={onSort} align="right" />
            <TableHead scope="col">Tier</TableHead>
            <TableHead scope="col">Env</TableHead>
            <TableHead scope="col">Trigger · runner · mode</TableHead>
            <TableHead scope="col">Tables</TableHead>
            <TableHead scope="col">Catalogue · evaluator</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((e) => {
            const isPicked = picked.has(e.evaluation_id);
            return (
              <TableRow
                key={e.evaluation_id}
                data-state={isPicked ? "selected" : undefined}
                data-evaluation={e.evaluation_id}
              >
                <TableCell className="sticky left-0 z-[1] w-10 bg-surface-1">
                  <input
                    type="checkbox"
                    checked={isPicked}
                    onChange={() => onPick(e.evaluation_id)}
                    aria-label={`Pick ${e.evaluation_id} for compare`}
                    className="size-4 cursor-pointer accent-accent"
                  />
                </TableCell>
                <th scope="row" className="sticky left-10 z-[1] bg-surface-1 px-3 py-2 text-left font-normal">
                  <Link
                    to="/evaluation/$evaluationId"
                    params={{ evaluationId: e.evaluation_id }}
                    className="font-mono text-sm whitespace-nowrap text-link hover:underline"
                  >
                    {e.evaluation_id}
                  </Link>
                </th>
                <TableCell className="text-xs whitespace-nowrap text-text-2">
                  {formatDateTime(e.evaluated_at)}
                </TableCell>
                <TableCell>
                  <StatusPill status={e.status} size="sm" />
                </TableCell>
                <TableCell className="text-right">
                  <ScoreCell score={e.overall_score} label="overall" />
                </TableCell>
                <TableCell className="text-right">
                  <ScoreCell score={e.fidelity_score} label="fidelity" />
                </TableCell>
                <TableCell className="text-right">
                  <ScoreCell score={e.privacy_score} label="privacy" />
                </TableCell>
                <TableCell className="text-right">
                  <ScoreCell score={e.integrity_score} label="integrity" />
                </TableCell>
                <TableCell className="text-right">
                  <ScoreCell score={e.diversity_score} label="diversity" />
                </TableCell>
                <TableCell>
                  <MetricsCell e={e} />
                </TableCell>
                <TableCell className="text-xs whitespace-nowrap">
                  {e.engine ?? MISSING} · <span className="text-text-2">{shortModel(e.llm_model_uri)}</span>
                </TableCell>
                <TableCell className="text-xs whitespace-nowrap text-text-2">
                  {e.embedder_id ?? MISSING} · {e.retrieval_method ?? MISSING}
                </TableCell>
                <TableCell className="text-right text-xs tabular-nums">{fmtSig(e.similarity)}</TableCell>
                <TableCell className="text-xs">{e.seed ?? MISSING}</TableCell>
                <TableCell className="text-right text-xs tabular-nums">
                  {formatCompact(e.reference_rows_limit)}
                </TableCell>
                <TableCell className="text-right text-xs tabular-nums">{formatCompact(e.num_rows_requested)}</TableCell>
                <TableCell className="text-xs">{e.source_stats_tier ?? MISSING}</TableCell>
                <TableCell className="text-xs">{e.env ?? MISSING}</TableCell>
                <TableCell className="text-xs whitespace-nowrap text-text-2">
                  {e.trigger ?? MISSING} · {e.runner?.replace("Runner", "") ?? MISSING} · {e.mode ?? MISSING}
                </TableCell>
                <TableCell
                  className="max-w-[14rem] truncate font-mono text-[11px] text-text-2"
                  title={e.tables.map((t) => t.name).join(", ")}
                >
                  {e.tables.map((t) => t.name).join(", ")}
                </TableCell>
                <TableCell className="font-mono text-[11px] whitespace-nowrap text-text-2">
                  {e.catalogue_version} · {e.evaluator_version}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function EvaluationCards({
  items,
  picked,
  onPick,
}: {
  items: EvaluationSummary[];
  picked: Set<string>;
  onPick: (id: string) => void;
}) {
  return (
    <ul className="grid gap-3" aria-label="Evaluations">
      {items.map((e) => (
        <li
          key={e.evaluation_id}
          data-evaluation={e.evaluation_id}
          className="grid gap-2 rounded-lg border border-border bg-surface-1 p-3"
        >
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={picked.has(e.evaluation_id)}
              onChange={() => onPick(e.evaluation_id)}
              aria-label={`Pick ${e.evaluation_id} for compare`}
              className="size-5 cursor-pointer accent-accent"
            />
            <Link
              to="/evaluation/$evaluationId"
              params={{ evaluationId: e.evaluation_id }}
              className="font-mono text-sm font-semibold text-link"
            >
              {e.evaluation_id}
            </Link>
            <StatusPill status={e.status} size="sm" className="ml-auto" />
          </div>
          <p className="text-xs text-text-2">
            {formatDateTime(e.evaluated_at)} · {e.engine ?? MISSING} · {shortModel(e.llm_model_uri)} ·{" "}
            {e.env ?? MISSING}
          </p>
          <dl className="grid grid-cols-5 gap-1 text-center">
            {(
              [
                ["Overall", e.overall_score],
                ["Fid.", e.fidelity_score],
                ["Priv.", e.privacy_score],
                ["Integ.", e.integrity_score],
                ["Div.", e.diversity_score],
              ] as const
            ).map(([label, score]) => (
              <div key={label} className="grid">
                <dt className="text-[10px] text-text-3">{label}</dt>
                <dd className="text-sm">
                  <ScoreCell score={score} label={label} />
                </dd>
              </div>
            ))}
          </dl>
          <MetricsCell e={e} />
        </li>
      ))}
    </ul>
  );
}

function SavedViews({
  search,
  onApply,
}: {
  search: EvaluationListSearch;
  onApply: (s: Partial<EvaluationListSearch>) => void;
}) {
  const [views, setViews] = useState<SavedView[]>(() => readSavedViews());
  const [name, setName] = useState("");
  const save = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const next = [
      ...views.filter((v) => v.name !== trimmed),
      { name: trimmed, search: { ...search, page: undefined, pick: undefined } },
    ];
    if (writeSavedViews(next)) {
      setViews(next);
      setName("");
      toast({ title: `View “${trimmed}” saved in this browser`, tone: "good" });
    } else
      toast({
        title: "This browser blocks storage",
        description: "Bookmark the URL instead: it holds every filter.",
        tone: "warn",
      });
  };
  const remove = (viewName: string) => {
    const next = views.filter((v) => v.name !== viewName);
    writeSavedViews(next);
    setViews(next);
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      toast({ title: "Link copied", description: "It reopens this exact view.", tone: "good" });
    } catch {
      toast({ title: "Copy failed", description: "Copy the address bar instead.", tone: "warn" });
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Views">
      <span className="inline-flex items-center gap-0.5 text-xs font-medium text-text-3">
        Views <InfoHint concept="eval:saved-views" />
      </span>
      {presetViews().map((view) => (
        <Button
          key={view.id}
          variant="outline"
          size="sm"
          onClick={() => onApply({ ...EMPTY_FILTERS, sort: undefined, order: undefined, ...view.search })}
        >
          {view.label}
        </Button>
      ))}
      {views.map((view) => (
        <span key={view.name} className="inline-flex items-center rounded-md border border-accent/40 bg-accent-soft">
          <button
            type="button"
            className="cursor-pointer px-2 py-1 text-xs text-accent-text"
            onClick={() => onApply({ ...EMPTY_FILTERS, ...view.search })}
          >
            <Bookmark className="mr-1 inline size-3" aria-hidden="true" />
            {view.name}
          </button>
          <button
            type="button"
            aria-label={`Delete view ${view.name}`}
            className="cursor-pointer px-1.5 py-1 text-text-3 hover:text-text-1"
            onClick={() => remove(view.name)}
          >
            <X className="size-3" aria-hidden="true" />
          </button>
        </span>
      ))}
      <label className="sr-only" htmlFor="save-view-name">
        Name for this view
      </label>
      <input
        id="save-view-name"
        value={name}
        onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => event.key === "Enter" && save()}
        placeholder="Name this view…"
        className="h-8 w-36 rounded-md border border-control-border bg-surface-1 px-2 text-xs text-text-1 placeholder:text-text-3"
      />
      <Button size="sm" variant="secondary" onClick={save} disabled={!name.trim()}>
        Save view
      </Button>
      <Button size="sm" variant="ghost" onClick={() => void copy()}>
        <Link2 aria-hidden="true" />
        Copy link
      </Button>
    </div>
  );
}

const WIDE_QUERY = "(min-width: 768px)";

function subscribeWide(onChange: () => void) {
  const query = window.matchMedia(WIDE_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** Table from md up, cards below: only one of the two is in the DOM (half the nodes for axe and React). */
function useWide(): boolean {
  return useSyncExternalStore(
    subscribeWide,
    () => window.matchMedia(WIDE_QUERY).matches,
    () => true,
  );
}

export function EvaluationListPage() {
  const search = route.useSearch();
  const wide = useWide();
  const navigate = route.useNavigate();
  const facets = useFacets();
  const filter = useMemo(() => toFilter(search), [search]);
  const list = useEvaluations(filter);
  const [moreOpen, setMoreOpen] = useState(() =>
    FACETS.some((f) => !f.primary && (search[f.key] as unknown[] | undefined)?.length),
  );

  const setSearch = useCallback(
    (patch: Partial<EvaluationListSearch>, options: { keepPage?: boolean } = {}) => {
      void navigate({
        search: (prev) => ({ ...prev, ...patch, ...(options.keepPage || "page" in patch ? {} : { page: undefined }) }),
        replace: true,
        resetScroll: false,
      });
    },
    [navigate],
  );
  const commitQuery = useCallback((q: string) => setSearch({ q: q || undefined }), [setSearch]);
  const picked = useMemo(() => new Set(search.pick ?? []), [search.pick]);
  const onPick = (id: string) => {
    const next = picked.has(id) ? [...picked].filter((p) => p !== id) : [...picked, id].slice(-12);
    setSearch({ pick: next.length ? next : undefined }, { keepPage: true });
  };
  const onSort = (sort: NonNullable<EvaluationListSearch["sort"]>) => {
    const same = (search.sort ?? "evaluated_at") === sort;
    setSearch({ sort, order: same ? ((search.order ?? "desc") === "desc" ? "asc" : "desc") : "desc" });
  };
  const page = list.data?.data;
  const total = page?.total ?? 0;
  const pageIndex = search.page ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const chips = activeChips(search, facets.data?.data);
  const latest = facets.data?.data.latest;
  const items = page?.items ?? [];
  const failing = items.filter((e) => (e.metrics_fail ?? 0) > 0).length;
  const worstPrivacy = items.reduce<EvaluationSummary | null>(
    (acc, e) => (e.privacy_score !== null && (acc === null || e.privacy_score < (acc.privacy_score ?? 2)) ? e : acc),
    null,
  );

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Evaluation"
        title="Evaluations"
        concept="eval:evaluation"
        description="Every evaluation of a generation launch: status, scores per family, and the parameters behind them. Filter by any facet, pick runs to compare, open one to see why it scored what it did."
        actions={
          <Button asChild={picked.size >= 2} variant="primary" disabled={picked.size < 2}>
            {picked.size >= 2 ? (
              <Link to="/evaluation/compare" search={{ ids: [...picked] }}>
                <GitCompareArrows aria-hidden="true" />
                Compare {picked.size}
              </Link>
            ) : (
              <>
                <GitCompareArrows aria-hidden="true" />
                Pick 2+ to compare
              </>
            )}
          </Button>
        }
      />

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <StatTile
          label="Latest evaluation"
          value={latest?.overall_score ?? null}
          format={(v) => fmtScore(v)}
          concept="core:score"
          footnote={
            latest ? (
              <Link
                to="/evaluation/$evaluationId"
                params={{ evaluationId: latest.evaluation_id }}
                className="text-link hover:underline"
              >
                {latest.evaluation_id} · {latest.status} · {formatDateTime(latest.evaluated_at)}
              </Link>
            ) : (
              "No evaluation yet"
            )
          }
        />
        <StatTile
          label="Evaluations in view"
          value={page ? total : null}
          format={formatCount}
          footnote={`of ${formatCount(facets.data?.data.counts.evaluations)} recorded`}
        />
        <StatTile
          label="With a failing metric"
          value={page ? failing : null}
          format={formatCount}
          footnote="on this page"
          concept="core:status"
        />
        <StatTile
          label="Lowest privacy score"
          value={worstPrivacy?.privacy_score ?? null}
          format={(v) => fmtScore(v)}
          footnote={worstPrivacy ? `${worstPrivacy.evaluation_id} (this page)` : "on this page"}
        />
      </div>

      <section aria-labelledby="filters-title" className="grid gap-3">
        <h2 id="filters-title" className="sr-only">
          Filters
        </h2>
        <SavedViews search={search} onApply={(s) => setSearch(s)} />
        {facets.data ? (
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
            <DebouncedSearch value={search.q ?? ""} onCommit={commitQuery} />
            {FACETS.filter((f) => f.primary || moreOpen).map((def) => (
              <FacetBox
                key={def.key}
                def={def}
                facets={facets.data.data}
                value={search[def.key] as (string | number)[] | undefined}
                onChange={(v) => setSearch({ [def.key]: v })}
              />
            ))}
            <label className="flex items-center gap-1.5 text-xs text-text-2">
              From
              <input
                type="date"
                value={search.from?.slice(0, 10) ?? ""}
                onChange={(event) => setSearch({ from: event.target.value || undefined })}
                className="h-9 rounded-md border border-control-border bg-surface-1 px-2 text-sm text-text-1"
              />
            </label>
            <label className="flex items-center gap-1.5 text-xs text-text-2">
              Before
              <input
                type="date"
                value={search.to?.slice(0, 10) ?? ""}
                onChange={(event) => setSearch({ to: event.target.value || undefined })}
                className="h-9 rounded-md border border-control-border bg-surface-1 px-2 text-sm text-text-1"
              />
            </label>
            {moreOpen ? (
              <>
                <label className="flex items-center gap-1.5 text-xs text-text-2">
                  Similarity ≥
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.05}
                    value={search.similarity_min ?? ""}
                    onChange={(event) =>
                      setSearch({ similarity_min: event.target.value === "" ? undefined : Number(event.target.value) })
                    }
                    className="h-9 w-20 rounded-md border border-control-border bg-surface-1 px-2 text-sm text-text-1"
                  />
                </label>
                <label className="flex items-center gap-1.5 text-xs text-text-2">
                  ≤
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.05}
                    value={search.similarity_max ?? ""}
                    onChange={(event) =>
                      setSearch({ similarity_max: event.target.value === "" ? undefined : Number(event.target.value) })
                    }
                    className="h-9 w-20 rounded-md border border-control-border bg-surface-1 px-2 text-sm text-text-1"
                    aria-label="Similarity at most"
                  />
                </label>
              </>
            ) : null}
            <Button variant="ghost" size="sm" onClick={() => setMoreOpen((v) => !v)} aria-expanded={moreOpen}>
              <SlidersHorizontal aria-hidden="true" />
              {moreOpen ? "Fewer filters" : "More filters"}
            </Button>
          </div>
        ) : (
          <Skeleton className="h-9 w-full" />
        )}
        {chips.length ? (
          <div className="flex flex-wrap items-center gap-1.5" aria-label="Active filters">
            {chips.map((chip) => (
              <Badge key={chip.id} variant="outline" className="gap-0.5 pr-0.5">
                {chip.label}
                <button
                  type="button"
                  aria-label={`Remove filter ${chip.label}`}
                  className="inline-flex size-5 cursor-pointer items-center justify-center rounded-full hover:bg-surface-3"
                  onClick={() => setSearch(chip.remove)}
                >
                  <X aria-hidden="true" />
                </button>
              </Badge>
            ))}
            <Button variant="link" size="sm" onClick={() => setSearch({ ...EMPTY_FILTERS })}>
              Clear all
            </Button>
          </div>
        ) : null}
      </section>

      <ScoreHistory search={search} />

      <section
        aria-labelledby="list-title"
        className={cn("grid gap-3 transition-opacity", list.isPlaceholderData && "opacity-60")}
      >
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="list-title" className="text-lg font-semibold tracking-tight text-text-1">
            {page ? `${formatCount(total)} evaluation${total === 1 ? "" : "s"}` : "Loading evaluations…"}
          </h2>
          {picked.size ? (
            <span className="text-xs text-text-3">
              {picked.size} picked for compare ·{" "}
              <button
                type="button"
                className="cursor-pointer text-link hover:underline"
                onClick={() => setSearch({ pick: undefined }, { keepPage: true })}
              >
                clear picks
              </button>
            </span>
          ) : (
            <span className="text-xs text-text-3">Tick two or more rows to compare them.</span>
          )}
          {list.data?.bytesEstimate !== null && list.data?.bytesEstimate !== undefined ? (
            <span className="text-xs text-text-3">· BigQuery dry run {formatCompact(list.data.bytesEstimate)} B</span>
          ) : null}
        </div>
        {list.data?.warnings.length ? (
          <Callout tone="info" title="Values newer than this GUI's contract">
            Shown as plain text: {list.data.warnings.join("; ")}
          </Callout>
        ) : null}
        {list.isPending ? (
          <Skeleton className="h-96 w-full" />
        ) : list.error ? (
          <EmptyState title="Evaluations could not be loaded" description={list.error.message} />
        ) : items.length ? (
          <>
            {wide ? (
              <EvaluationsTable items={items} search={search} picked={picked} onPick={onPick} onSort={onSort} />
            ) : (
              <EvaluationCards items={items} picked={picked} onPick={onPick} />
            )}
            <nav aria-label="Pages" className="flex items-center gap-2 text-sm text-text-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={pageIndex === 0}
                onClick={() => setSearch({ page: pageIndex - 1 || undefined })}
              >
                <ChevronLeft aria-hidden="true" />
                Previous
              </Button>
              <span className="tabular-nums">
                Page {pageIndex + 1} of {pages}
              </span>
              <Button
                variant="secondary"
                size="sm"
                disabled={pageIndex + 1 >= pages}
                onClick={() => setSearch({ page: pageIndex + 1 })}
              >
                Next
                <ChevronRight aria-hidden="true" />
              </Button>
            </nav>
          </>
        ) : (
          <EmptyState
            title="No evaluation matches these filters"
            description="Remove a filter or widen the date range."
            action={
              <Button variant="secondary" onClick={() => setSearch({ ...EMPTY_FILTERS })}>
                Clear all filters
              </Button>
            }
          />
        )}
      </section>
    </div>
  );
}
