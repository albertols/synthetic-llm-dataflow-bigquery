/**
 * /evaluation/compare?ids=… — two to twelve evaluations side by side.
 *
 * Reads top-down: can these runs be compared at all (catalogue, evaluator,
 * encoding plans — a prominent banner when not)? how do the families move
 * (radar, Pareto, trend small multiples with noise and threshold bands)?
 * which parameters go with which scores (parallel coordinates)? and metric
 * by metric, A against B, with every delta below the noise floor shown as
 * "≈" and never judged better or worse.
 */
import { getRouteApi, Link } from "@tanstack/react-router";
import { ArrowLeftRight, GitCompareArrows, X } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import type { ComparedMetric, Comparison } from "@contracts/api";

import { Banner, Callout } from "@/components/Callout";
import { ChartFrame } from "@/components/ChartFrame";
import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { LevelChip } from "@/components/LevelChip";
import { PageHeader } from "@/components/PageHeader";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useCompare, useEvaluations } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatCompact, formatDateTime, MISSING } from "@/lib/format";

import { FAMILIES, FAMILY_LABEL, isLevel, metricConcept } from "./lib/catalogue";
import {
  diffMetric,
  encodingDiffers,
  pairReasons,
  VERDICT_LABEL,
  type DiffResult,
  type DiffVerdict,
} from "./lib/compare";
import {
  metricLabel,
  paretoSpec,
  parallelSpec,
  radarSpec,
  topMovers,
  trendSpec,
  type ColorBy,
} from "./lib/compareCharts";
import { fmtDelta, fmtMetric, fmtScore, shortModel } from "./lib/format";
import { statusRank } from "./lib/model";
import { useChartTokens } from "./lib/tokens";
import type { EvaluationCompareSearch } from "./route";

const route = getRouteApi("/evaluation/compare");

const VERDICT_GLYPH: Record<DiffVerdict, string> = {
  approx: "≈",
  better: "▲",
  worse: "▼",
  same: "=",
  missing: "∅",
  not_evaluated: "–",
  unjudged: "?",
};

function Swatch({ index }: { index: number }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block size-2.5 shrink-0 rounded-full"
      style={{ background: index < 8 ? `var(--chart-${index + 1})` : "var(--chart-other)" }}
    />
  );
}

function Picker({ ids, onChange }: { ids: string[]; onChange: (ids: string[]) => void }) {
  const options = useEvaluations({ limit: 200, sort: "evaluated_at", order: "desc" });
  const choices = (options.data?.data.items ?? []).map((e) => ({
    value: e.evaluation_id,
    label: e.evaluation_id,
    description: `${formatDateTime(e.evaluated_at)} · ${e.engine ?? MISSING} · ${shortModel(e.llm_model_uri)} · overall ${fmtScore(e.overall_score)}`,
  }));
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Evaluations to compare">
      {ids.map((id, i) => (
        <span
          key={id}
          className="inline-flex items-center gap-1.5 rounded-full border border-border-strong bg-surface-2 py-0.5 pr-0.5 pl-2 text-xs"
        >
          <Swatch index={i} />
          <Link
            to="/evaluation/$evaluationId"
            params={{ evaluationId: id }}
            className="font-mono text-link hover:underline"
          >
            {id}
          </Link>
          <button
            type="button"
            aria-label={`Remove ${id}`}
            onClick={() => onChange(ids.filter((x) => x !== id))}
            className="inline-flex size-6 cursor-pointer items-center justify-center rounded-full text-text-3 hover:bg-surface-3 hover:text-text-1"
          >
            <X className="size-3" aria-hidden="true" />
          </button>
        </span>
      ))}
      {ids.length < 12 ? (
        <Combobox
          label="Add an evaluation"
          options={choices.filter((c) => !ids.includes(c.value))}
          value={null}
          onValueChange={(value) => value && onChange([...ids, value])}
          placeholder="Add an evaluation…"
          className="w-64"
        />
      ) : null}
    </div>
  );
}

function RunsTable({ comparison }: { comparison: Comparison }) {
  return (
    <TableContainer aria-label="Compared evaluations">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead scope="col">Evaluation</TableHead>
            <TableHead scope="col">Evaluated</TableHead>
            <TableHead scope="col">Status</TableHead>
            <TableHead scope="col">Engine · model</TableHead>
            <TableHead scope="col">
              <span className="inline-flex items-center gap-0.5">
                Catalogue · evaluator <InfoHint concept="eval:comparable" />
              </span>
            </TableHead>
            <TableHead scope="col" className="text-right">
              Overall
            </TableHead>
            {FAMILIES.map((f) => (
              <TableHead key={f} scope="col" className="text-right">
                {FAMILY_LABEL[f]}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {comparison.evaluations.map((e, i) => (
            <TableRow key={e.evaluation_id}>
              <th scope="row" className="px-3 py-2 text-left font-normal">
                <span className="inline-flex items-center gap-2">
                  <Swatch index={i} />
                  <Link
                    to="/evaluation/$evaluationId"
                    params={{ evaluationId: e.evaluation_id }}
                    className="font-mono text-sm whitespace-nowrap text-link hover:underline"
                  >
                    {e.evaluation_id}
                  </Link>
                </span>
              </th>
              <TableCell className="text-xs whitespace-nowrap text-text-2">{formatDateTime(e.evaluated_at)}</TableCell>
              <TableCell>
                <StatusPill status={e.status} size="sm" />
              </TableCell>
              <TableCell className="text-xs whitespace-nowrap">
                {e.engine ?? MISSING} · {shortModel(e.llm_model_uri)}
              </TableCell>
              <TableCell className="font-mono text-xs whitespace-nowrap">
                {e.catalogue_version} · {e.evaluator_version}
              </TableCell>
              <TableCell className="text-right text-sm font-semibold tabular-nums">
                {fmtScore(e.overall_score)}
              </TableCell>
              {FAMILIES.map((f) => (
                <TableCell key={f} className="text-right text-sm tabular-nums">
                  {fmtScore(e[`${f}_score`])}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function RunMetricHeatmap({ comparison, family }: { comparison: Comparison; family?: string }) {
  const [limit, setLimit] = useState(40);
  const rows = useMemo(() => {
    const list = comparison.metrics.filter((m) => !family || family === "overall" || m.family === family);
    const spread = (m: ComparedMetric) => {
      const ranks = m.cells.map((c) => statusRank(c?.status));
      return Math.max(...ranks) - Math.min(...ranks);
    };
    return [...list].sort(
      (a, b) =>
        Math.min(...a.cells.map((c) => statusRank(c?.status))) -
          Math.min(...b.cells.map((c) => statusRank(c?.status))) ||
        spread(b) - spread(a) ||
        a.key.localeCompare(b.key),
    );
  }, [comparison.metrics, family]);
  const glyph: Record<string, string> = { pass: "✓", warn: "!", fail: "✕", info: "i", not_evaluated: "–" };
  const tone: Record<string, string> = {
    pass: "bg-status-good/10",
    warn: "bg-status-warn/20",
    fail: "bg-status-critical/25 font-semibold",
    info: "bg-status-info/12",
    not_evaluated: "bg-status-neutral/15",
  };
  return (
    <section aria-labelledby="run-metric-title" className="grid gap-3">
      <h2 id="run-metric-title" className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1">
        Metrics × runs
        <InfoHint concept="eval:heatmap" />
        <span className="ml-2 text-xs font-normal text-text-3">
          {rows.length} metrics · worst first, then the ones whose status differs most
        </span>
      </h2>
      <TableContainer aria-label="Metric by run heatmap" className="max-h-[32rem]">
        <Table className="w-auto min-w-full">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead scope="col" className="sticky left-0 z-20 bg-surface-2">
                Metric
              </TableHead>
              {comparison.evaluations.map((e, i) => (
                <TableHead key={e.evaluation_id} scope="col" className="text-right">
                  <span className="inline-flex items-center gap-1 font-mono">
                    <Swatch index={i} />
                    {e.evaluation_id}
                  </span>
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.slice(0, limit).map((m) => (
              <TableRow key={m.key}>
                <th
                  scope="row"
                  className="sticky left-0 z-[1] max-w-[22rem] truncate bg-surface-1 px-3 py-1 text-left text-xs font-normal text-text-1"
                  title={metricLabel(m)}
                >
                  {metricLabel(m)}
                </th>
                {m.cells.map((c, i) => (
                  <td
                    key={i}
                    data-status={c?.status ?? "absent"}
                    className={cn(
                      "px-2 py-1 text-right font-mono text-[11px] whitespace-nowrap tabular-nums",
                      c ? tone[c.status] : "",
                    )}
                  >
                    {c ? (
                      <>
                        <span aria-hidden="true" className="mr-1 text-text-2">
                          {glyph[c.status] ?? "?"}
                        </span>
                        <span className="sr-only">{c.status}, </span>
                        {c.value === null ? "n/e" : fmtMetric(c.value, m.value_kind)}
                      </>
                    ) : (
                      <span className="text-text-3">absent</span>
                    )}
                  </td>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      {rows.length > limit ? (
        <Button variant="secondary" size="sm" className="justify-self-start" onClick={() => setLimit((l) => l + 80)}>
          Show {Math.min(80, rows.length - limit)} more of {rows.length}
        </Button>
      ) : null}
    </section>
  );
}

interface DiffRow {
  metric: ComparedMetric;
  diff: DiffResult;
}

function ABDiff({
  comparison,
  a,
  b,
  rowsMode,
  family,
  onPick,
  onRows,
}: {
  comparison: Comparison;
  a: number;
  b: number;
  rowsMode: "changed" | "all" | "not_comparable";
  family?: string;
  onPick: (patch: { a?: string; b?: string }) => void;
  onRows: (mode: "changed" | "all" | "not_comparable") => void;
}) {
  const [limit, setLimit] = useState(60);
  const A = comparison.evaluations[a]!;
  const B = comparison.evaluations[b]!;
  const rows = useMemo<DiffRow[]>(() => {
    const versionReasons = pairReasons(A, B);
    const encodingTables = encodingDiffers(comparison, a, b);
    return comparison.metrics
      .filter((m) => !family || family === "overall" || m.family === family)
      .map((metric) => ({ metric, diff: diffMetric(metric, a, b, { versionReasons, encodingTables }) }));
  }, [comparison, a, b, A, B, family]);
  const counts = useMemo(() => {
    const c: Record<DiffVerdict, number> = {
      approx: 0,
      better: 0,
      worse: 0,
      same: 0,
      missing: 0,
      not_evaluated: 0,
      unjudged: 0,
    };
    for (const r of rows) c[r.diff.verdict] += 1;
    return c;
  }, [rows]);
  const notComparable = rows.filter((r) => r.diff.notComparable.length).length;
  const shown = rows
    .filter((r) =>
      rowsMode === "all"
        ? true
        : rowsMode === "not_comparable"
          ? r.diff.notComparable.length > 0
          : r.diff.verdict === "better" ||
            r.diff.verdict === "worse" ||
            r.diff.verdict === "unjudged" ||
            (r.diff.verdict !== "approx" && r.diff.verdict !== "same" && r.diff.a?.status !== r.diff.b?.status),
    )
    .sort((x, y) => {
      const rank = (r: DiffRow) =>
        r.diff.verdict === "worse" ? 0 : r.diff.verdict === "better" ? 1 : r.diff.verdict === "unjudged" ? 2 : 3;
      return rank(x) - rank(y) || Math.abs(y.diff.delta ?? 0) - Math.abs(x.diff.delta ?? 0);
    });
  const ids = comparison.evaluations.map((e) => e.evaluation_id);
  return (
    <section aria-labelledby="ab-title" className="grid gap-3">
      <h2 id="ab-title" className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1">
        A/B diff
        <InfoHint concept="eval:approx" />
      </h2>
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-text-2" htmlFor="ab-a">
          A
        </label>
        <Select value={A.evaluation_id} onValueChange={(v) => onPick({ a: v })}>
          <SelectTrigger id="ab-a" className="w-40 font-mono">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ids.map((id) => (
              <SelectItem key={id} value={id}>
                {id}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Swap A and B"
          onClick={() => onPick({ a: B.evaluation_id, b: A.evaluation_id })}
        >
          <ArrowLeftRight aria-hidden="true" />
        </Button>
        <label className="flex items-center gap-2 text-sm text-text-2" htmlFor="ab-b">
          B
        </label>
        <Select value={B.evaluation_id} onValueChange={(v) => onPick({ b: v })}>
          <SelectTrigger id="ab-b" className="w-40 font-mono">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ids.map((id) => (
              <SelectItem key={id} value={id}>
                {id}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <ToggleGroup
          type="single"
          value={rowsMode}
          onValueChange={(v) => v && onRows(v as "changed" | "all" | "not_comparable")}
          aria-label="Rows to show"
        >
          <ToggleGroupItem value="changed">Changed beyond noise</ToggleGroupItem>
          <ToggleGroupItem value="not_comparable">Not comparable</ToggleGroupItem>
          <ToggleGroupItem value="all">All</ToggleGroupItem>
        </ToggleGroup>
      </div>
      <p className="text-sm text-text-2" data-testid="ab-summary">
        B against A: <strong className="text-text-1">{counts.better} better</strong>,{" "}
        <strong className="text-text-1">{counts.worse} worse</strong>, {counts.approx} ≈ within noise, {counts.same}{" "}
        identical, {counts.unjudged} not judged
        {notComparable ? ` (${notComparable} rows not comparable)` : ""}, {counts.missing + counts.not_evaluated}{" "}
        missing or not evaluated.
      </p>
      <TableContainer aria-label="A/B diff" className="max-h-[36rem]">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead scope="col">Metric</TableHead>
              <TableHead scope="col">Level</TableHead>
              <TableHead scope="col" className="text-right">
                A · {A.evaluation_id}
              </TableHead>
              <TableHead scope="col" className="text-right">
                B · {B.evaluation_id}
              </TableHead>
              <TableHead scope="col" className="text-right">
                Δ (B − A)
              </TableHead>
              <TableHead scope="col" className="text-right">
                Noise floor
              </TableHead>
              <TableHead scope="col">Verdict</TableHead>
              <TableHead scope="col">Status A → B</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.slice(0, limit).map(({ metric, diff }) => {
              const concept = metricConcept(metric.metric_id);
              return (
                <TableRow
                  key={metric.key}
                  data-verdict={diff.verdict}
                  data-not-comparable={diff.notComparable.length > 0 || undefined}
                >
                  <th scope="row" className="px-3 py-1.5 text-left text-xs font-normal">
                    <span className="inline-flex items-center gap-0.5">
                      <span className="max-w-[20rem] truncate text-text-1" title={metricLabel(metric)}>
                        {metricLabel(metric)}
                      </span>
                      {concept ? <InfoHint concept={concept} /> : null}
                    </span>
                  </th>
                  <TableCell>
                    {isLevel(metric.level) ? <LevelChip level={metric.level} size="sm" /> : metric.level}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {fmtMetric(diff.a?.value ?? null, metric.value_kind)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {fmtMetric(diff.b?.value ?? null, metric.value_kind)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {diff.verdict === "approx" ? <span className="text-text-3">≈ </span> : null}
                    {fmtDelta(diff.delta, metric.value_kind)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs text-text-2 tabular-nums">
                    {fmtMetric(diff.floor, metric.value_kind)}
                  </TableCell>
                  <TableCell className="text-xs whitespace-nowrap">
                    <span
                      aria-hidden="true"
                      className={cn(
                        "mr-1",
                        diff.verdict === "worse" && "text-status-critical-text",
                        diff.verdict === "better" && "text-status-good-text",
                      )}
                    >
                      {VERDICT_GLYPH[diff.verdict]}
                    </span>
                    {VERDICT_LABEL[diff.verdict]}
                    {diff.basis === "ci_overlap" ? " (CIs overlap)" : ""}
                    {diff.notComparable.length ? (
                      <span
                        className="ml-1.5 inline-flex rounded-sm border border-status-critical/55 bg-status-critical/14 px-1 text-[10px] font-medium text-status-critical-text"
                        title={diff.notComparable.join("; ")}
                      >
                        not comparable
                      </span>
                    ) : null}
                    {diff.notComparable.length ? (
                      <span className="sr-only">: {diff.notComparable.join("; ")}</span>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-xs whitespace-nowrap">
                    <span className="inline-flex items-center gap-1">
                      {diff.a ? (
                        <StatusPill status={diff.a.status} size="sm" />
                      ) : (
                        <span className="text-text-3">absent</span>
                      )}
                      <span aria-hidden="true">→</span>
                      <span className="sr-only">to</span>
                      {diff.b ? (
                        <StatusPill status={diff.b.status} size="sm" />
                      ) : (
                        <span className="text-text-3">absent</span>
                      )}
                    </span>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
      {!shown.length ? (
        <p className="text-sm text-text-2">No metric changed beyond its noise floor between A and B.</p>
      ) : null}
      {shown.length > limit ? (
        <Button variant="secondary" size="sm" className="justify-self-start" onClick={() => setLimit((l) => l + 100)}>
          Show {Math.min(100, shown.length - limit)} more of {shown.length}
        </Button>
      ) : null}
    </section>
  );
}

function ParamsDiff({ comparison }: { comparison: Comparison }) {
  const [all, setAll] = useState(false);
  const groups = useMemo(() => {
    const rows = comparison.params.filter((p) => all || p.differs);
    return [
      {
        id: "generation",
        title: "generation_params",
        rows: rows.filter((p) => p.path.startsWith("generation_params.")),
      },
      {
        id: "evaluation",
        title: "evaluation_params",
        rows: rows.filter((p) => p.path.startsWith("evaluation_params.")),
      },
      { id: "typed", title: "Registry columns", rows: rows.filter((p) => !p.path.includes(".")) },
    ];
  }, [comparison.params, all]);
  const differing = comparison.params.filter((p) => p.differs).length;
  const show = (v: unknown) =>
    v === null || v === undefined ? "null" : v === "" ? '""' : typeof v === "string" ? v : JSON.stringify(v);
  return (
    <section aria-labelledby="params-diff-title" className="grid gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="params-diff-title" className="text-lg font-semibold tracking-tight text-text-1">
          Parameters diff
        </h2>
        <span className="text-xs text-text-3">
          {differing} of {comparison.params.length} parameters differ
        </span>
        <Button variant="ghost" size="sm" onClick={() => setAll((v) => !v)}>
          {all ? "Only differences" : "Show all parameters"}
        </Button>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {groups.map((g) => (
          <TableContainer key={g.id} aria-label={`${g.title} diff`} className="max-h-[26rem]">
            <Table>
              <caption className="px-3 py-2 text-left font-mono text-xs text-text-2">{g.title}</caption>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead scope="col">Path</TableHead>
                  {comparison.evaluations.map((e, i) => (
                    <TableHead key={e.evaluation_id} scope="col">
                      <span className="inline-flex items-center gap-1 font-mono">
                        <Swatch index={i} />
                        {e.evaluation_id}
                      </span>
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {g.rows.length ? (
                  g.rows.map((p) => {
                    const first = show(p.values[0]);
                    return (
                      <TableRow key={p.path} data-differs={p.differs || undefined}>
                        <th scope="row" className="px-3 py-1 text-left font-mono text-xs font-normal text-text-1">
                          {p.path.replace(/^(generation|evaluation)_params\./, "")}
                        </th>
                        {p.values.map((v, i) => {
                          const text = show(v);
                          const changed = p.differs && text !== first;
                          return (
                            <TableCell
                              key={i}
                              className={cn(
                                "max-w-[16rem] truncate font-mono text-xs",
                                changed ? "bg-accent-soft text-accent-text" : "text-text-2",
                              )}
                              title={text}
                            >
                              {changed ? <span className="sr-only">changed: </span> : null}
                              {text}
                            </TableCell>
                          );
                        })}
                      </TableRow>
                    );
                  })
                ) : (
                  <TableRow>
                    <TableCell colSpan={comparison.evaluations.length + 1} className="text-xs text-text-3">
                      No differences.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
        ))}
      </div>
    </section>
  );
}

export function EvaluationComparePage() {
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const ids = useMemo(() => [...new Set(search.ids)], [search.ids]);
  const compare = useCompare(ids);
  const history = useEvaluations({ limit: 500, sort: "evaluated_at", order: "desc" });
  const tokens = useChartTokens();
  const colorBy: ColorBy = search.color ?? "engine";
  const setSearch = useCallback(
    (patch: Partial<EvaluationCompareSearch>) =>
      void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true, resetScroll: false }),
    [navigate],
  );
  const comparison = compare.data?.data;
  const charts = useMemo(() => {
    if (!comparison || comparison.evaluations.length < 1) return null;
    const idx = comparison.evaluations.map((_, i) => i);
    const trendIds = [
      "model.overall_score",
      "model.fidelity_score",
      "model.privacy_score",
      "model.integrity_score",
      "model.diversity_score",
    ];
    const trends = [
      ...trendIds
        .map((id) => comparison.metrics.find((m) => m.metric_id === id))
        .filter((m): m is ComparedMetric => !!m),
      ...topMovers(comparison, 4),
    ].map((metric) => ({ metric, spec: trendSpec(metric, comparison.evaluations, colorBy, tokens) }));
    return {
      radar: radarSpec(comparison, idx, tokens),
      pareto: paretoSpec(comparison, history.data?.data.items ?? [], colorBy, tokens),
      parallel: parallelSpec(comparison, colorBy, tokens),
      trends,
    };
  }, [comparison, history.data, colorBy, tokens]);

  const quickPair = (history.data?.data.items ?? [])
    .filter((e) => e.status !== "RUNNING")
    .slice(0, 2)
    .map((e) => e.evaluation_id)
    .reverse();

  const header = (
    <PageHeader
      eyebrow="Evaluation"
      title="Compare evaluations"
      concept="eval:comparable"
      description="Scores, metrics and parameters side by side. Deltas below the noise floor read ≈; runs measured with a different catalogue, evaluator or encoding plan are flagged, never judged."
    />
  );

  if (ids.length < 2) {
    return (
      <div className="flex flex-col gap-6">
        {header}
        <Picker ids={ids} onChange={(next) => setSearch({ ids: next })} />
        <EmptyState
          icon={GitCompareArrows}
          title={ids.length ? "Add one more evaluation" : "Pick two or more evaluations"}
          description="Use the picker above, or tick rows in the evaluations list."
          action={
            quickPair.length === 2 ? (
              <Button
                variant="secondary"
                onClick={() => setSearch({ ids: [...new Set([...ids, ...quickPair])].slice(0, 12) })}
              >
                Compare the two newest
              </Button>
            ) : undefined
          }
        />
      </div>
    );
  }

  if (compare.isPending) {
    return (
      <div className="flex flex-col gap-6" role="status" aria-busy="true">
        {header}
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }
  if (compare.error || !comparison) {
    return (
      <div className="flex flex-col gap-6">
        {header}
        <Picker ids={ids} onChange={(next) => setSearch({ ids: next })} />
        <EmptyState
          title="The comparison could not be loaded"
          description={compare.error?.message ?? "Unknown error"}
        />
      </div>
    );
  }

  const n = comparison.evaluations.length;
  if (n < 2) {
    return (
      <div className="flex flex-col gap-6">
        {header}
        <Picker ids={ids} onChange={(next) => setSearch({ ids: next, a: undefined, b: undefined })} />
        {comparison.missing.length ? (
          <Callout tone="warn" title="Unknown evaluation ids">
            {comparison.missing.join(", ")} — not in the registry.
          </Callout>
        ) : null}
        <EmptyState
          icon={GitCompareArrows}
          title="Fewer than two of these evaluations exist"
          description="Pick existing evaluations with the picker above, or from the evaluations list."
        />
      </div>
    );
  }
  const indexOf = (id: string | undefined, fallback: number) => {
    const i = id ? comparison.evaluations.findIndex((e) => e.evaluation_id === id) : -1;
    return i >= 0 ? i : fallback;
  };
  const a = indexOf(search.a, 0);
  let b = indexOf(search.b, Math.min(1, n - 1));
  if (b === a) b = a === 0 ? Math.min(1, n - 1) : 0;
  const notComparable = comparison.comparability.not_comparable;

  return (
    <div className={cn("flex flex-col gap-6", compare.isPlaceholderData && "opacity-70")}>
      {header}
      <Picker ids={ids} onChange={(next) => setSearch({ ids: next, a: undefined, b: undefined })} />
      {notComparable.length ? (
        <Banner tone="danger" title="Not directly comparable" live={false}>
          <span className="inline-flex flex-wrap items-center gap-0.5">
            Deltas between these runs may measure the ruler, not the data. Each run&apos;s own statuses still hold.
            <InfoHint concept="eval:comparable" />
          </span>
          <ul className="mt-1 list-disc pl-5" data-testid="not-comparable-reasons">
            {notComparable.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </Banner>
      ) : (
        <Callout tone="info" title="Comparable">
          Same catalogue ({comparison.evaluations[0]?.catalogue_version}), same evaluator (
          {comparison.evaluations[0]?.evaluator_version}) and the same encoding plan for every table.
        </Callout>
      )}
      {comparison.missing.length ? (
        <Callout tone="warn" title="Unknown evaluation ids">
          {comparison.missing.join(", ")} — not in the registry; they are left out.
        </Callout>
      ) : null}

      <RunsTable comparison={comparison} />
      {compare.data?.warnings.length ? (
        <Callout tone="info" title="Values newer than this GUI's contract">
          Shown as plain text: {compare.data.warnings.join("; ")}
        </Callout>
      ) : null}
      {compare.data?.bytesEstimate !== null && compare.data?.bytesEstimate !== undefined ? (
        <p className="text-xs text-text-3">
          BigQuery bytes for this comparison: {formatCompact(compare.data.bytesEstimate)} B (dry run).
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-3" role="group" aria-label="Chart options">
        <span className="text-sm text-text-2">Colour runs by</span>
        <ToggleGroup
          type="single"
          value={colorBy}
          onValueChange={(v) => v && setSearch({ color: v as ColorBy })}
          aria-label="Colour runs by"
        >
          <ToggleGroupItem value="engine">Engine</ToggleGroupItem>
          <ToggleGroupItem value="model">LLM model</ToggleGroupItem>
        </ToggleGroup>
        <span className="text-sm text-text-2">Family</span>
        <ToggleGroup
          type="single"
          className="max-w-full overflow-x-auto"
          value={search.family ?? "overall"}
          onValueChange={(v) =>
            v && setSearch({ family: v === "overall" ? undefined : (v as EvaluationCompareSearch["family"]) })
          }
          aria-label="Family filter for the tables"
        >
          <ToggleGroupItem value="overall">All</ToggleGroupItem>
          {FAMILIES.map((f) => (
            <ToggleGroupItem key={f} value={f}>
              {FAMILY_LABEL[f]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartFrame
          title="Family scores"
          concept="core:family"
          description={
            n > 3
              ? `The first three runs (a fourth polygon is unreadable); the table above carries all ${n}.`
              : "Model roll-up per family; axes from 0.5 to 1."
          }
          option={charts?.radar?.option ?? {}}
          data={charts?.radar?.data ?? []}
          empty={{ when: !charts?.radar, message: "No family scores to draw." }}
          height={300}
        />
        <ChartFrame
          title="Fidelity vs privacy"
          concept="eval:pareto"
          description="x = fidelity score, y = privacy score. Compared runs in colour, every other evaluation in grey; the step line is the frontier (no run beats it on both)."
          option={charts?.pareto?.option ?? {}}
          data={charts?.pareto?.data ?? []}
          empty={{ when: !charts?.pareto, message: "Fidelity and privacy scores are missing." }}
          height={300}
        />
      </div>

      <section aria-labelledby="trends-title" className="grid gap-3">
        <h2 id="trends-title" className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1">
          Trends
          <InfoHint concept="core:noise-floor" />
          <span className="ml-2 text-xs font-normal text-text-3">
            runs in time order · grey band = noise floor · amber / red = warn / fail
          </span>
        </h2>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {(charts?.trends ?? []).map(({ metric, spec }) => (
            <ChartFrame
              key={metric.key}
              title={metricLabel(metric)}
              concept={metricConcept(metric.metric_id)}
              option={spec?.option ?? {}}
              data={spec?.data ?? []}
              empty={{ when: !spec, message: "Not evaluated in these runs." }}
              height={190}
            />
          ))}
        </div>
      </section>

      <ChartFrame
        title="Parameters → scores"
        concept="eval:parallel"
        description="One line per run across the parameters that differ and the five scores."
        option={charts?.parallel?.option ?? {}}
        data={charts?.parallel?.data ?? []}
        empty={{ when: !charts?.parallel, message: "Needs two or more runs." }}
        height={320}
      />

      <ABDiff
        comparison={comparison}
        a={a}
        b={b}
        rowsMode={search.rows ?? "changed"}
        family={search.family}
        onPick={(patch) => setSearch(patch)}
        onRows={(rows) => setSearch({ rows })}
      />
      <RunMetricHeatmap comparison={comparison} family={search.family} />
      <ParamsDiff comparison={comparison} />
    </div>
  );
}
