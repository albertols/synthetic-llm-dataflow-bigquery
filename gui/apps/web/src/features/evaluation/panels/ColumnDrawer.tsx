/**
 * The column drawer: everything the evaluator measured on one column. Its
 * profiles load when the drawer opens (`/api/evaluations/:id/profiles?table&column`),
 * never with the run, so a 200-column evaluation stays fast.
 *
 * Numeric / temporal: histogram overlay (source, synthetic, reference R),
 * ECDF with the KS gap marker and the noise band, Q–Q plot, deciles,
 * moments, calendar mixes. Categorical / text: top-k (hashed labels under
 * D6), lengths, shapes, character classes. Every kind: null / empty / zero
 * rates with Wilson intervals, entropy and distinct counts at matched n, and
 * every metric's readout.
 */
import { useMemo, type ReactNode } from "react";

import type { MetricRow, ProfileRow } from "@contracts/api";
import { parseProfile, type ProfileKind, type ProfilePayload } from "@contracts/payloads";
import { wilson } from "@synthetic-platform/stats/intervals";

import { ChartFrame } from "@/components/ChartFrame";
import { DataTableFallback } from "@/components/DataTableFallback";
import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { StatusPill } from "@/components/StatusPill";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfiles } from "@/lib/api";
import { formatCount, formatDate, MISSING } from "@/lib/format";

import { IntervalPlot, type IntervalRow } from "../components/IntervalPlot";
import { MetricReadout } from "../components/MetricReadout";
import { NoiseLegend } from "../components/NoiseLegend";
import { VisualFrame } from "../components/VisualFrame";
import { metricShort } from "../lib/catalogue";
import { ecdfChart, histogramOverlay, pairedBars, qqChart, topkItems } from "../lib/charts";
import { fmtMetric, fmtShare, fmtSig } from "../lib/format";
import { interpretRow } from "../lib/interpret";
import { countsPhrase, countsTotal, statusRank, type ColumnSummary } from "../lib/model";
import { niceTicks } from "../lib/scale";
import { useChartTokens } from "../lib/tokens";

type Profiles = Map<string, ProfileRow>;

function pick<K extends ProfileKind>(profiles: Profiles, kind: K, side: string): ProfilePayload<K> | null {
  const row = profiles.get(`${kind}|${side}`);
  return row ? parseProfile(kind, row.payload) : null;
}

function has(profiles: Profiles, kind: string): boolean {
  return profiles.has(`${kind}|source`) || profiles.has(`${kind}|synthetic`);
}

const UNAVAILABLE =
  "Profile unavailable: not stored for this column, or its payload does not match this GUI's reading.";

function Section({
  id,
  title,
  concept,
  children,
}: {
  id: string;
  title: string;
  concept?: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="grid gap-3">
      <h3 id={id} className="flex items-center gap-0.5 text-sm font-semibold text-text-1">
        {title}
        {concept ? <InfoHint concept={concept} /> : null}
      </h3>
      {children}
    </section>
  );
}

function NumericProfiles({
  profiles,
  metrics,
  drawerId,
}: {
  profiles: Profiles;
  metrics: Map<string, MetricRow>;
  drawerId: string;
}) {
  const tokens = useChartTokens();
  const ks = metrics.get("column.ks");
  const hist = useMemo(
    () =>
      histogramOverlay(
        {
          source: pick(profiles, "histogram", "source"),
          synthetic: pick(profiles, "histogram", "synthetic"),
          reference: pick(profiles, "histogram", "reference"),
        },
        tokens,
      ),
    [profiles, tokens],
  );
  const ecdf = useMemo(
    () =>
      ecdfChart(
        pick(profiles, "histogram", "source"),
        pick(profiles, "histogram", "synthetic"),
        ks?.noise_floor ?? null,
        tokens,
      ),
    [profiles, tokens, ks],
  );
  const qq = useMemo(
    () => qqChart(pick(profiles, "quantiles", "source"), pick(profiles, "quantiles", "synthetic"), tokens),
    [profiles, tokens],
  );
  const deciles = useMemo(() => {
    const s = pick(profiles, "quantiles", "source");
    const y = pick(profiles, "quantiles", "synthetic");
    if (!s || !y) return [];
    const temporal = s.unit === "epoch_seconds";
    const f = (v: number | undefined) => (v === undefined ? MISSING : temporal ? formatDate(v * 1000) : fmtSig(v));
    return s.probs
      .map((p, i) => ({ p, i }))
      .filter(({ p }) => p > 0 && p < 1 && Math.abs(p * 10 - Math.round(p * 10)) < 1e-9)
      .map(({ p, i }) => ({
        decile: `p${Math.round(p * 100)}`,
        source: f(s.values[i]),
        synthetic: f(y.values[y.probs.indexOf(p)]),
        delta: temporal
          ? `${fmtSig(((y.values[y.probs.indexOf(p)] ?? 0) - (s.values[i] ?? 0)) / 86400)} d`
          : fmtSig((y.values[y.probs.indexOf(p)] ?? 0) - (s.values[i] ?? 0)),
      }));
  }, [profiles]);
  const moments = useMemo(() => {
    const s = pick(profiles, "moments", "source");
    const y = pick(profiles, "moments", "synthetic");
    if (!s || !y) return [];
    const keys = ["n", "mean", "std", "skewness", "kurtosis_excess", "min", "max", "zeros"] as const;
    const temporal = s.unit === "epoch_seconds";
    const f = (k: (typeof keys)[number], v: number | null) =>
      v === null
        ? MISSING
        : temporal && (k === "mean" || k === "min" || k === "max")
          ? formatDate(v * 1000)
          : k === "n" || k === "zeros"
            ? formatCount(v)
            : fmtSig(v);
    return keys.map((k) => ({ moment: k, source: f(k, s[k]), synthetic: f(k, y[k]) }));
  }, [profiles]);
  const mix = pick(profiles, "temporal_mix", "source");
  const mixY = pick(profiles, "temporal_mix", "synthetic");
  const mixCharts = useMemo(() => {
    if (!mix || !mixY) return [];
    const toShares = (xs: readonly number[]) => {
      const total = xs.reduce((a, b) => a + b, 0) || 1;
      return xs.map((x) => x / total);
    };
    const build = (name: string, labels: string[], a: readonly number[], b: readonly number[]) => {
      const sa = toShares(a);
      const sb = toShares(b);
      return {
        name,
        spec: pairedBars(
          labels.map((label, i) => ({ label, source: sa[i] ?? 0, synthetic: sb[i] ?? 0 })),
          tokens,
          { max: 24, categoryName: name },
        ),
      };
    };
    const out = [
      build("weekday", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], mix.dow, mixY.dow),
      build(
        "month",
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        mix.month,
        mixY.month,
      ),
    ];
    if (!mix.day_granularity && mix.hour.length === 24 && mixY.hour.length === 24)
      out.push(
        build(
          "hour",
          Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, "0")}h`),
          mix.hour,
          mixY.hour,
        ),
      );
    return out;
  }, [mix, mixY, tokens]);
  const ksDetail = ks?.detail as { d_lo?: number; d_hi?: number } | null | undefined;
  return (
    <>
      <ChartFrame
        title="Distribution: source, synthetic and the reference sample"
        concept="eval:ecdf"
        description="Share of rows per bin on the source's equiprobable edges; the line is the reference sample R the generator read."
        option={hist?.option ?? {}}
        data={hist?.data ?? []}
        empty={{ when: !hist, message: UNAVAILABLE }}
        height={260}
      />
      <ChartFrame
        title="ECDF with the KS gap and the noise band"
        concept="eval:ks-bracket"
        description={
          ks
            ? `KS ${fmtMetric(ks.value, "distance")} (bracket ${fmtSig(ksDetail?.d_lo ?? ks.value)}–${fmtSig(ksDetail?.d_hi ?? null)}); band = source CDF ± noise floor ${fmtMetric(ks.noise_floor, "distance")}${ecdf?.gap ? `; widest gap at ${ecdf.gap.label}` : ""}.`
            : "CDFs at the bin edges."
        }
        option={ecdf?.option ?? {}}
        data={ecdf?.data ?? []}
        empty={{ when: !ecdf, message: UNAVAILABLE }}
        height={260}
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartFrame
          title="Q–Q plot"
          concept="eval:qq-plot"
          description="x = source quantile, y = synthetic quantile at the same probability; the grey line is y = x."
          option={qq?.option ?? {}}
          data={qq?.data ?? []}
          empty={{ when: !qq, message: UNAVAILABLE }}
          height={240}
        />
        <div className="grid content-start gap-2 rounded-lg border border-border bg-surface-1 p-4">
          <p className="text-sm font-semibold text-text-1" id={`${drawerId}-deciles`}>
            Deciles
          </p>
          {deciles.length ? (
            <DataTableFallback caption="Deciles, source vs synthetic" rows={deciles} />
          ) : (
            <p className="text-xs text-text-3">{UNAVAILABLE}</p>
          )}
        </div>
      </div>
      {moments.length ? <DataTableFallback caption="Moments, source vs synthetic" rows={moments} maxRows={10} /> : null}
      {mixCharts.length ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {mixCharts.map(({ name, spec }) => (
            <ChartFrame
              key={name}
              title={`Calendar mix: ${name}`}
              concept={
                name === "weekday"
                  ? "metric:column.dow_tvd"
                  : name === "month"
                    ? "metric:column.month_tvd"
                    : "metric:column.hour_tvd"
              }
              option={spec?.option ?? {}}
              data={spec?.data ?? []}
              empty={{ when: !spec, message: UNAVAILABLE }}
              height={name === "hour" ? 420 : name === "month" ? 300 : 220}
            />
          ))}
        </div>
      ) : null}
    </>
  );
}

function CategoricalProfiles({ profiles }: { profiles: Profiles }) {
  const tokens = useChartTokens();
  const topSource = pick(profiles, "topk", "source");
  const topSynthetic = pick(profiles, "topk", "synthetic");
  const topk = useMemo(
    () => pairedBars(topkItems(topSource, topSynthetic), tokens, { max: 12, categoryName: "value" }),
    [topSource, topSynthetic, tokens],
  );
  const hashed = topSource?.items.some((i) => !i.literal) ?? false;
  const lengths = useMemo(() => {
    const s = pick(profiles, "length_hist", "source");
    const y = pick(profiles, "length_hist", "synthetic");
    if (!s || !y) return null;
    const total = (p: { counts: number[]; overflow: number }) => p.counts.reduce((a, b) => a + b, 0) + p.overflow || 1;
    const ts = total(s);
    const ty = total(y);
    const all = [...new Set([...s.lengths, ...y.lengths])].sort((a, b) => a - b);
    const items = all.map((len) => ({
      label: String(len),
      source: (s.counts[s.lengths.indexOf(len)] ?? 0) / ts,
      synthetic: (y.counts[y.lengths.indexOf(len)] ?? 0) / ty,
    }));
    if (s.overflow || y.overflow) items.push({ label: "≥ 256", source: s.overflow / ts, synthetic: y.overflow / ty });
    return pairedBars(items, tokens, { max: 40, categoryName: "length" });
  }, [profiles, tokens]);
  const shapes = useMemo(() => {
    const s = pick(profiles, "shape_mix", "source");
    const y = pick(profiles, "shape_mix", "synthetic");
    if (!s || !y) return null;
    const syn = new Map(y.items.map((i) => [i.mask, i.share]));
    const src = new Map(s.items.map((i) => [i.mask, i.share]));
    const masks = [...s.items.map((i) => i.mask), ...y.items.map((i) => i.mask).filter((m) => !src.has(m))];
    const items = masks.map((mask) => ({ label: mask, source: src.get(mask) ?? 0, synthetic: syn.get(mask) ?? 0 }));
    items.push({ label: "(tail)", source: s.tail_share, synthetic: y.tail_share });
    return pairedBars(items, tokens, { max: 10, categoryName: "mask" });
  }, [profiles, tokens]);
  const classes = useMemo(() => {
    const s = pick(profiles, "char_classes", "source");
    const y = pick(profiles, "char_classes", "synthetic");
    if (!s || !y) return null;
    const keys = ["upper", "lower", "digit", "space", "punct", "other"] as const;
    return pairedBars(
      keys.map((k) => ({ label: k, source: s.classes[k], synthetic: y.classes[k] })),
      tokens,
      { categoryName: "class" },
    );
  }, [profiles, tokens]);
  return (
    <>
      <ChartFrame
        title="Top values"
        concept="eval:hashed-labels"
        description={
          topSource
            ? `${formatCount(topSource.distinct)} distinct source values${topSource.nulls ? `, ${formatCount(topSource.nulls)} nulls` : ""}; ${hashed ? "h:xxxxxxxx labels are salted hashes (literal policy D6)." : "labels are literal (≤ 50 source-distinct values, each seen ≥ 10 times)."}`
            : undefined
        }
        option={topk?.option ?? {}}
        data={topk?.data ?? []}
        empty={{ when: !topk, message: UNAVAILABLE }}
        height={Math.max(220, (topk?.data.length ?? 0) * 26 + 60)}
      />
      <div className="grid gap-4 lg:grid-cols-2">
        {has(profiles, "length_hist") ? (
          <ChartFrame
            title="String lengths"
            concept="metric:column.length_ks"
            option={lengths?.option ?? {}}
            data={lengths?.data ?? []}
            empty={{ when: !lengths, message: UNAVAILABLE }}
            height={Math.max(200, (lengths?.data.length ?? 0) * 22 + 60)}
          />
        ) : null}
        {has(profiles, "shape_mix") ? (
          <ChartFrame
            title="Shape mix (masks)"
            concept="metric:column.shape_head_tv"
            description="9 = digit, A = upper, a = lower; other characters literal."
            option={shapes?.option ?? {}}
            data={shapes?.data ?? []}
            empty={{ when: !shapes, message: UNAVAILABLE }}
            height={Math.max(200, (shapes?.data.length ?? 0) * 24 + 60)}
          />
        ) : null}
        {has(profiles, "char_classes") ? (
          <ChartFrame
            title="Character classes"
            concept="metric:column.char_class_l1"
            description="Share of values containing at least one character of each class."
            option={classes?.option ?? {}}
            data={classes?.data ?? []}
            empty={{ when: !classes, message: UNAVAILABLE }}
            height={230}
          />
        ) : null}
      </div>
    </>
  );
}

const RATE_METRICS = [
  ["column.null_rate_delta", "Null rate"],
  ["column.empty_rate_delta", "Empty-string rate"],
  ["column.zero_rate_delta", "Zero rate"],
] as const;

/** Null / empty / zero rates as source and synthetic Wilson intervals, from the delta metrics' source and synthetic values. */
export function rateIntervals(metrics: Map<string, MetricRow>): IntervalRow[] {
  const rows: IntervalRow[] = [];
  for (const [id, label] of RATE_METRICS) {
    const m = metrics.get(id);
    if (!m) continue;
    const sides = [
      { side: "source", rate: m.source_value, n: m.n_source },
      { side: "synthetic", rate: m.synthetic_value, n: m.n_synthetic },
    ] as const;
    for (const { side, rate, n } of sides) {
      if (rate === null || n === null || n <= 0) {
        rows.push({
          key: `${id}-${side}`,
          label: `${label} · ${side}`,
          value: null,
          lo: null,
          hi: null,
          status: m.status,
          detail: "not measured",
        });
        continue;
      }
      const [lo, hi] = wilson(Math.round(rate * n), n);
      rows.push({
        key: `${id}-${side}`,
        label: `${label} · ${side}`,
        sublabel: `n = ${formatCount(n)}`,
        value: rate,
        lo,
        hi,
        status: m.status,
        detail: `${fmtShare(rate)} [${fmtShare(lo)}, ${fmtShare(hi)}]`,
      });
    }
  }
  return rows;
}

function RatesSection({ metrics }: { metrics: Map<string, MetricRow> }) {
  const rows = rateIntervals(metrics);
  if (!rows.length) return null;
  const max = Math.max(0.02, ...rows.map((r) => r.hi ?? r.value ?? 0)) * 1.15;
  const scale = { kind: "linear" as const, lo: 0, hi: Math.min(1, max) };
  return (
    <VisualFrame
      title="Null, empty and zero rates with Wilson intervals"
      concept="eval:wilson"
      description="Each side's rate with its 95% Wilson interval; overlapping intervals are not told apart at this n. The status is the delta metric's."
      data={rows.map((r) => ({
        rate: r.label,
        n: r.sublabel ?? MISSING,
        value: r.value,
        wilson_low: r.lo,
        wilson_high: r.hi,
        status: r.status,
      }))}
    >
      <IntervalPlot rows={rows} scale={scale} lines={[]} ticks={niceTicks(scale, 4)} formatTick={(x) => fmtShare(x)} />
    </VisualFrame>
  );
}

function MatchedNSection({ metrics }: { metrics: Map<string, MetricRow> }) {
  const entropy = metrics.get("column.entropy_ratio");
  const distinct = metrics.get("column.distinct_ratio");
  if (!entropy && !distinct) return null;
  const matched = (row?: MetricRow) => (row?.detail as { matched_n?: number } | null | undefined)?.matched_n ?? null;
  return (
    <Section id="drawer-matched" title="Diversity at matched n" concept="eval:matched-n">
      <p className="text-xs text-text-2">
        Both sides subsampled to the same n before comparing
        {matched(entropy ?? distinct) !== null ? ` (n = ${formatCount(matched(entropy ?? distinct))})` : ""}.
        {entropy && entropy.source_value !== null && entropy.synthetic_value !== null
          ? ` Entropy: source ${fmtSig(entropy.source_value)} bits, synthetic ${fmtSig(entropy.synthetic_value)} bits.`
          : ""}
        {distinct && distinct.source_value !== null && distinct.synthetic_value !== null
          ? ` Distinct values: source ${formatCount(distinct.source_value)}, synthetic ${formatCount(distinct.synthetic_value)}.`
          : ""}
      </p>
      <div className="grid gap-3 lg:grid-cols-2">
        {entropy ? <MetricReadout row={entropy} showScope={false} headingLevel={4} /> : null}
        {distinct ? <MetricReadout row={distinct} showScope={false} headingLevel={4} /> : null}
      </div>
    </Section>
  );
}

export function ColumnDrawer({
  evaluationId,
  columnKey,
  summary,
  onClose,
}: {
  evaluationId: string;
  columnKey: string | undefined;
  summary: ColumnSummary | undefined;
  onClose: () => void;
}) {
  const open = columnKey !== undefined;
  const profilesQuery = useProfiles(
    evaluationId,
    { table: summary?.table, column: summary?.column },
    open && summary !== undefined,
  );
  const profiles = useMemo<Profiles>(() => {
    const map: Profiles = new Map();
    for (const row of profilesQuery.data?.data ?? []) map.set(`${row.profile_kind}|${row.side}`, row);
    return map;
  }, [profilesQuery.data]);
  const metrics = useMemo(() => new Map((summary?.metrics ?? []).map((m) => [m.metric_id, m])), [summary]);
  const sorted = useMemo(
    () =>
      [...(summary?.metrics ?? [])].sort(
        (a, b) => statusRank(a.status) - statusRank(b.status) || a.metric_id.localeCompare(b.metric_id),
      ),
    [summary],
  );
  const problems = sorted.filter((m) => m.status === "fail" || m.status === "warn" || m.status === "not_evaluated");
  const numeric = summary?.kind === "numeric" || summary?.kind === "temporal" || has(profiles, "histogram");
  const drawerId = "column-drawer";
  return (
    <Sheet open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <SheetContent side="right" className="gap-5 sm:max-w-3xl lg:max-w-5xl" aria-describedby={`${drawerId}-desc`}>
        <SheetHeader>
          <SheetTitle className="flex flex-wrap items-center gap-2 font-mono">
            {columnKey ?? ""}
            {summary?.kind ? <Badge variant="outline">{summary.kind}</Badge> : null}
            {summary?.worst ? <StatusPill status={summary.worst} size="sm" /> : null}
          </SheetTitle>
          <SheetDescription id={`${drawerId}-desc`}>
            {summary
              ? `${countsTotal(summary.counts)} field- and column-level metrics: ${countsPhrase(summary.counts)}. Profiles load on open.`
              : "This column has no metrics in this evaluation."}
          </SheetDescription>
        </SheetHeader>
        {summary ? (
          <>
            <NoiseLegend className="rounded-md border border-border bg-surface-1 px-3 py-2" />
            {problems.length ? (
              <Section id={`${drawerId}-verdict`} title="What to look at" concept="eval:interpretation">
                <ul className="grid gap-1.5 text-sm text-text-2">
                  {problems.map((m) => (
                    <li key={m.metric_id} className="flex items-start gap-2">
                      <StatusPill status={m.status} size="sm" />
                      <span>{interpretRow(m)}</span>
                    </li>
                  ))}
                </ul>
              </Section>
            ) : (
              <p className="text-sm text-text-2">Every metric on this column passes.</p>
            )}
            <Section id={`${drawerId}-profiles`} title="Profiles">
              {profilesQuery.isPending ? (
                <div className="grid gap-3" role="status" aria-busy="true">
                  <span className="sr-only">Loading profiles</span>
                  <Skeleton className="h-56 w-full" />
                  <Skeleton className="h-56 w-full" />
                </div>
              ) : profilesQuery.error ? (
                <EmptyState
                  compact
                  headingLevel="none"
                  title="Profiles could not be loaded"
                  description={String(profilesQuery.error.message)}
                />
              ) : profiles.size === 0 ? (
                <EmptyState
                  compact
                  headingLevel="none"
                  title="No profiles stored for this column"
                  description="The metrics below still hold every number the evaluator computed."
                />
              ) : numeric ? (
                <NumericProfiles profiles={profiles} metrics={metrics} drawerId={drawerId} />
              ) : (
                <CategoricalProfiles profiles={profiles} />
              )}
            </Section>
            <RatesSection metrics={metrics} />
            <MatchedNSection metrics={metrics} />
            <Section id={`${drawerId}-metrics`} title={`All metrics (${sorted.length})`} concept="eval:status-rule">
              <div className="grid gap-3 lg:grid-cols-2">
                {sorted.map((row) => (
                  <MetricReadout key={row.metric_id} row={row} showScope={false} />
                ))}
              </div>
              <p className="text-[11px] text-text-3">
                Field metrics check each value ({metricShort("field.type_validity")}, adherence, copies); column metrics
                compare distributions.
              </p>
            </Section>
          </>
        ) : (
          <EmptyState
            compact
            headingLevel="none"
            title="Column not found"
            description="The link names a column this evaluation did not measure."
          />
        )}
      </SheetContent>
    </Sheet>
  );
}
