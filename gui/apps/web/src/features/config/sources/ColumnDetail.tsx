/**
 * One column of the selected snapshots: the quantile function with the DKW
 * band (sample tier), top values under the literal policy, the shape mix,
 * lengths, temporal mixes and — for `__table__` — the row null patterns.
 * With two tiers of one digest, each chart overlays both.
 */
import type { EChartsOption } from "echarts";
import { useMemo, type ReactNode } from "react";

import type { SourceStatsColumn, SourceStatsSnapshot } from "@contracts/api";
import type { ProfilerStats } from "@contracts/sourceStats";

import { Callout } from "@/components/Callout";
import { ChartFrame } from "@/components/ChartFrame";
import { InfoHint } from "@/components/InfoHint";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatCount, formatDateTime, formatFixed, formatPercent, MISSING } from "@/lib/format";
import { useTheme } from "@/lib/theme";

import { lineOption, pctFormat, themed } from "../charts";
import { knobNumber } from "../model/knobs";
import { TierBadge } from "./TierBadge";
import { decodeNullPattern, DOW, isHashedLabel, isTableRow, MONTHS, quantileRows, type Tier } from "./sourceModel";

type TierRow = { tier: Tier; stats: ProfilerStats | null; row: SourceStatsColumn; snapshot?: SourceStatsSnapshot };

export function ColumnDetail({
  column,
  rows,
  snapshots,
  sameDigest,
}: {
  column: string;
  rows: SourceStatsColumn[];
  snapshots: SourceStatsSnapshot[];
  sameDigest: boolean;
}) {
  const tiers = useMemo<TierRow[]>(
    () =>
      rows
        .map((row) => ({
          tier: row.tier,
          stats: row.stats_parsed,
          row,
          snapshot: snapshots.find((s) => s.key === row.snapshot_key),
        }))
        .sort((a, b) => (a.tier === b.tier ? 0 : a.tier === "sample" ? -1 : 1)),
    [rows, snapshots],
  );
  const first = tiers[0];
  if (!first) return null;
  const table = isTableRow(column);
  const kind = columnKind(tiers);

  return (
    <section aria-labelledby="src-column" className="grid gap-4" data-testid="column-detail">
      <div className="grid gap-1">
        <h2 id="src-column" className="flex flex-wrap items-center gap-2 text-lg font-semibold text-text-1">
          <span className="font-mono">{column}</span>
          {tiers.map((t) => (t.snapshot ? <TierBadge key={t.row.snapshot_key} snapshot={t.snapshot} /> : null))}
        </h2>
        <p className="text-sm text-text-2">
          {table
            ? "The table's row-level entry: which columns are null together."
            : `Plan ${first.row.generation_plan ?? MISSING} · type ${first.stats?.type ?? MISSING}${first.row.is_pk ? " · PK" : ""}${first.row.is_fk ? " · FK" : ""}.`}
          {tiers.length === 2 && !sameDigest ? " The two tiers come from different digests." : ""}
        </p>
      </div>
      {tiers.every((t) => !t.stats) ? (
        <Callout tone="warn" title="No profiler entry">
          The stats JSON of this row does not parse as a profiler entry; only the broken-out fractions are shown above.
        </Callout>
      ) : table ? (
        <NullPatterns tiers={tiers} />
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {kind.numeric ? <Quantiles tiers={tiers} /> : null}
          {kind.string ? <TopValues tiers={tiers} /> : null}
          {kind.string ? <Shapes tiers={tiers} /> : null}
          <Temporal tiers={tiers} />
          <Lengths tiers={tiers} />
        </div>
      )}
    </section>
  );
}

const tierName = (t: TierRow) => `${t.tier} tier`;

const NUMERIC_TYPES = new Set(["INT64", "INTEGER", "FLOAT64", "FLOAT", "NUMERIC", "BIGNUMERIC"]);

/** Which charts apply: deciles for numeric columns, values and shapes for strings (or whatever the entry carries). */
function columnKind(tiers: TierRow[]) {
  const types = tiers.map((t) => (t.stats?.type ?? "").toUpperCase());
  return {
    numeric: types.some((t) => NUMERIC_TYPES.has(t)) || tiers.some((t) => t.stats?.deciles?.length),
    string:
      types.some((t) => t === "STRING" || t === "BOOL" || t === "BOOLEAN") ||
      tiers.some((t) => t.stats?.top_values?.length || t.stats?.shape_mix?.length),
  };
}

function Quantiles({ tiers }: { tiers: TierRow[] }) {
  const { resolved } = useTheme();
  const sample = tiers.find((t) => t.tier === "sample" && t.stats?.deciles?.length);
  const exact = tiers.find((t) => t.tier === "exact" && t.stats?.deciles?.length);
  const rows = useMemo(
    () =>
      quantileRows(
        sample ? { deciles: sample.stats!.deciles!, rows: sample.row.sample_rows ?? 0 } : null,
        exact ? { deciles: exact.stats!.deciles! } : null,
      ),
    [sample, exact],
  );
  const option = useMemo(
    () =>
      lineOption(
        {
          x: "p",
          xName: "rank p",
          yName: "value",
          series: [
            ...(sample ? [{ name: "sample tier", y: "sample" }] : []),
            ...(exact ? [{ name: "exact tier", y: "exact" }] : []),
          ],
          band: sample ? { base: "bandBase", span: "bandSpan", name: "DKW band (sample)" } : undefined,
          xFormatter: pctFormat,
        },
        resolved,
      ),
    [sample, exact, resolved],
  );
  const empty = !sample && !exact;
  return (
    <ChartFrame
      title="Quantile function (11 stored deciles)"
      concept="stats:deciles"
      description={
        sample
          ? "Shaded: where the true quantile lies at 95 % (DKW band in rank space, interpolated between the stored points)."
          : "Exact tier: full-table deciles, no sampling band."
      }
      option={option}
      data={empty ? [] : rows}
      empty={{ when: empty, message: "No deciles: this column is not numeric." }}
      height={240}
      columns={[
        { key: "p", format: (v) => pctFormat(v as number), align: "right" },
        ...(sample
          ? [{ key: "sample", format: (v: unknown) => formatFixed(v as number, 2), align: "right" as const }]
          : []),
        ...(exact
          ? [{ key: "exact", format: (v: unknown) => formatFixed(v as number, 2), align: "right" as const }]
          : []),
      ]}
    />
  );
}

type ShareKey = "top_values" | "shape_mix" | "null_pattern_mix";

/**
 * Grouped horizontal bars of [label, share] pairs, one series per tier, built
 * in one memo (rows and option together) so the chart re-renders only when
 * the data or the theme change.
 */
function useTierShares(tiers: TierRow[], key: ShareKey, label: (raw: string) => string, labelWidth: number) {
  const { resolved } = useTheme();
  return useMemo(() => {
    const withData = tiers.filter((t) => t.stats?.[key]?.length);
    const raw = [...new Set(withData.flatMap((t) => t.stats![key]!.map(([v]) => v)))];
    const rows = raw.map((value) => {
      const row: Record<string, unknown> = { label: label(value) };
      for (const t of withData) row[t.tier] = t.stats![key]!.find(([v]) => v === value)?.[1] ?? 0;
      return row;
    });
    const option = themed<EChartsOption>(
      {
        grid: { left: 8, right: 16, top: withData.length > 1 ? 32 : 12, bottom: 8, containLabel: true },
        ...(withData.length > 1 ? { legend: { type: "scroll", top: 0, left: 0, right: 0 } } : {}),
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
        xAxis: { type: "value", axisLabel: { formatter: pctFormat } },
        yAxis: { type: "category", inverse: true, axisLabel: { width: labelWidth, overflow: "truncate" } },
        series: withData.map((t) => ({
          type: "bar" as const,
          name: tierName(t),
          encode: { y: "label", x: t.tier },
          barMaxWidth: 14,
          itemStyle: { borderRadius: [0, 4, 4, 0] },
        })),
      },
      resolved,
    );
    return { rows, option, raw };
  }, [tiers, key, label, labelWidth, resolved]);
}

const literalLabel = (value: string) => (isHashedLabel(value) ? `${value} (hashed)` : value);
const identity = (value: string) => value;

function TopValues({ tiers }: { tiers: TierRow[] }) {
  const { rows, option } = useTierShares(tiers, "top_values", literalLabel, 140);
  const distinct = tiers[0]?.stats?.distinct ?? 0;
  const cap = knobNumber("top_values_max_distinct");
  return (
    <ChartFrame
      title="Top values"
      concept="core:literal-policy"
      description={`Literal values only for enum-like columns (≤ ${cap} distinct); share of non-empty rows.`}
      option={option}
      data={rows}
      height={Math.max(160, rows.length * 26 + 48)}
      empty={{
        when: rows.length === 0,
        message:
          distinct > cap
            ? `No literal values: ${formatCount(distinct)} distinct is above the ${cap}-value policy cap (shapes and entropy describe it instead).`
            : "No top values recorded for this column.",
      }}
    />
  );
}

function Shapes({ tiers }: { tiers: TierRow[] }) {
  const { rows, option } = useTierShares(tiers, "shape_mix", identity, 170);
  return (
    <ChartFrame
      title="Shape mix"
      concept="stats:shape-mix"
      description="9 = digit, A = upper case, a = lower case; the format, never the value."
      option={option}
      data={rows}
      height={Math.max(160, rows.length * 26 + 48)}
      empty={{ when: rows.length === 0, message: "No shape mix: this column is not a string." }}
    />
  );
}

function mixRows(tiers: TierRow[], key: "dow_mix" | "hour_mix" | "month_mix", labels: (i: number) => string) {
  const withMix = tiers.filter((t) => t.stats?.[key]?.length);
  const length = Math.max(0, ...withMix.map((t) => t.stats![key]!.length));
  return {
    tiers: withMix,
    rows: Array.from({ length }, (_, i) => {
      const row: Record<string, unknown> = { bucket: labels(i) };
      for (const t of withMix) row[t.tier] = t.stats![key]![i] ?? 0;
      return row;
    }),
  };
}

function Temporal({ tiers }: { tiers: TierRow[] }) {
  const { resolved } = useTheme();
  const dow = mixRows(tiers, "dow_mix", (i) => DOW[i] ?? String(i));
  const hour = mixRows(tiers, "hour_mix", (i) => `${String(i).padStart(2, "0")}h`);
  const month = mixRows(tiers, "month_mix", (i) => MONTHS[i] ?? String(i));
  const mixes = [
    { title: "Day of week", ...dow },
    { title: "Hour of day", ...hour },
    { title: "Month", ...month },
  ].filter((m) => m.rows.length);
  const st = tiers[0]?.stats;
  const option = (m: (typeof mixes)[number]) =>
    themed<EChartsOption>(
      {
        grid: { left: 8, right: 16, top: m.tiers.length > 1 ? 32 : 12, bottom: 8, containLabel: true },
        ...(m.tiers.length > 1 ? { legend: { type: "scroll", top: 0, left: 0, right: 0 } } : {}),
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
        xAxis: { type: "category" },
        yAxis: { type: "value", axisLabel: { formatter: pctFormat } },
        series: m.tiers.map((t) => ({
          type: "bar" as const,
          name: tierName(t),
          encode: { x: "bucket", y: t.tier },
          barMaxWidth: 16,
        })),
      },
      resolved,
    );
  if (!mixes.length) return null;
  return (
    <Card className="xl:col-span-2">
      <CardHeader>
        <CardTitle className="flex items-center gap-1">
          Temporal mixes <InfoHint concept="stats:temporal-mix" />
        </CardTitle>
        <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <Fact label="Earliest" value={st?.temporal_min ? formatDateTime(st.temporal_min) : MISSING} />
          <Fact label="Latest" value={st?.temporal_max ? formatDateTime(st.temporal_max) : MISSING} />
          <Fact label="In the future" value={formatPercent(st?.future_fraction ?? null, 2)} />
          <Fact label="Day granularity" value={st?.temporal_day_granularity ? "yes" : "no"} />
        </dl>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-3">
        {mixes.map((m) => (
          <ChartFrame key={m.title} title={m.title} option={option(m)} data={m.rows} height={200} />
        ))}
      </CardContent>
    </Card>
  );
}

function Lengths({ tiers }: { tiers: TierRow[] }) {
  const withLengths = tiers.filter((t) => t.stats && (t.stats.len_p50 ?? null) !== null);
  if (!withLengths.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1">
          Lengths <InfoHint concept="knob:length_percentiles" />
        </CardTitle>
        <p className="text-sm text-text-2">The p05–p95 band becomes the pool prompt's length hint.</p>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-3">
          {withLengths.map((t) => (
            <div key={t.row.snapshot_key} className="grid gap-1">
              <dt className="text-xs text-text-3">{tierName(t)}</dt>
              <dd className="flex flex-wrap gap-x-4 font-mono text-sm text-text-1">
                <span>p05 {t.stats!.len_p05 ?? MISSING}</span>
                <span>p50 {t.stats!.len_p50 ?? MISSING}</span>
                <span>p95 {t.stats!.len_p95 ?? MISSING}</span>
                <span>mean {formatFixed(t.stats!.mean_len ?? null, 1)}</span>
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

function NullPatterns({ tiers }: { tiers: TierRow[] }) {
  const columns = useMemo(
    () => tiers.find((t) => t.stats?.null_pattern_columns?.length)?.stats?.null_pattern_columns ?? [],
    [tiers],
  );
  const label = useMemo(
    () => (bits: string) => {
      const nulls = decodeNullPattern(bits, columns);
      return nulls.length ? `null: ${nulls.join(", ")}` : "no nulls";
    },
    [columns],
  );
  const { rows, option } = useTierShares(tiers, "null_pattern_mix", label, 200);
  return (
    <div className="grid gap-3">
      <ChartFrame
        title="Row null patterns"
        concept="stats:null-patterns"
        description={`Top patterns over ${columns.length} columns (bit i = column i is null).`}
        option={option}
        data={rows}
        height={Math.max(140, rows.length * 30 + 48)}
        empty={{
          when: rows.length === 0,
          message: "No null patterns: the table is wider than the profiler's column cap, or nothing is null.",
        }}
      />
      <p className="text-xs text-text-3">Columns in bit order: {columns.join(", ") || MISSING}.</p>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="text-text-3">{label}</dt>
      <dd className="font-mono text-text-1">{value}</dd>
    </div>
  );
}
