/**
 * Privacy: memorization, exposure, near-match, value and pool lifts as a
 * forest plot (point, 95 % interval, thresholds; the gate reads ci_low), the
 * raw copy rates, the DCR holdout test (syn→R vs H→R histograms and the
 * closer-to-reference share with its Wilson band), NNDR, and the flagged
 * rows by key (source keys hashed). Every similarity-based number here is a
 * risk indicator, not a privacy guarantee — the panel says so up front.
 */
import { useMemo, useState } from "react";

import type { EvaluationRecord, MetricRow, ProfileRow, RowFlag } from "@contracts/api";
import { parseProfile } from "@contracts/payloads";

import { Callout } from "@/components/Callout";
import { ChartFrame } from "@/components/ChartFrame";
import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useProfiles } from "@/lib/api";
import { formatCount, MISSING } from "@/lib/format";

import { IntervalPlot, type IntervalRow, type RefLine } from "../components/IntervalPlot";
import { MetricReadout } from "../components/MetricReadout";
import { VisualFrame } from "../components/VisualFrame";
import { metricShort } from "../lib/catalogue";
import { pairedHistogram } from "../lib/charts";
import { fmtMetric, fmtShare, fmtSig, scopeLabel } from "../lib/format";
import { LIFT_METRICS, PRIVACY_RATE_METRICS, statusRank } from "../lib/model";
import { downgradedFrom, readingOf, reasonOf, undefinedValueText } from "../lib/reading";
import { useChartTokens } from "../lib/tokens";

const LOG_TICKS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500];

/** Forest rows for the lifts: the point estimate, the rate-ratio interval, and the counts behind it. */
export function liftRows(metrics: readonly MetricRow[], table?: string): IntervalRow[] {
  return metrics
    .filter((m) => (LIFT_METRICS as readonly string[]).includes(m.metric_id) && (!table || m.table_name === table))
    .sort((a, b) => statusRank(a.status) - statusRank(b.status) || (b.ci_low ?? -1) - (a.ci_low ?? -1))
    .map((m) => {
      const detail = (m.detail ?? {}) as { copies_r?: number; copies_h?: number };
      const counts =
        detail.copies_r !== undefined && detail.copies_h !== undefined
          ? ` · m_R/m_H ${formatCount(detail.copies_r)}/${formatCount(detail.copies_h)}`
          : "";
      const skipped = m.status === "not_evaluated";
      // A NULL value — undefined (no copies) or infinite (none in the holdout) — still gates on ci_low (Ruling R38).
      const point = m.value === null ? undefinedValueText({ ciLow: m.ci_low }).split(":")[0]! : `${fmtSig(m.value)}×`;
      const text = skipped
        ? `not evaluated — ${reasonOf(m) ?? "no reason recorded"}`
        : `${point} · ci_low ${fmtSig(m.ci_low)}${m.ci_high === null && m.ci_low !== null ? " · open above" : ""}${counts}`;
      return {
        key: `${m.metric_id}|${scopeLabel(m)}`,
        label: metricShort(m.metric_id),
        sublabel: scopeLabel(m),
        value: m.value,
        lo: skipped ? null : m.ci_low,
        hi: skipped ? null : m.ci_high,
        status: m.status,
        downgradedFrom: downgradedFrom(m),
        detail: text,
      };
    });
}

function logDomain(rows: readonly IntervalRow[]) {
  const finite = rows
    .flatMap((r) => [r.value, r.lo, r.hi])
    .filter((v): v is number => v !== null && Number.isFinite(v) && v > 0);
  const hi = Math.min(500, Math.max(10, ...finite) * 1.3);
  return { kind: "log" as const, lo: 0.1, hi };
}

function Lifts({ metrics, table }: { metrics: MetricRow[]; table?: string }) {
  const rows = useMemo(() => liftRows(metrics, table), [metrics, table]);
  const [all, setAll] = useState(false);
  const scale = logDomain(rows);
  const sample = metrics.find((m) => (LIFT_METRICS as readonly string[]).includes(m.metric_id));
  const warn = sample?.threshold_warn ?? 2;
  const fail = sample?.threshold_fail ?? 5;
  const lines: RefLine[] = [
    { x: 1, label: "1× none", tone: "ref" },
    { x: warn, label: `warn ${fmtSig(warn)}×`, tone: "warn" },
    { x: fail, label: `fail ${fmtSig(fail)}×`, tone: "fail" },
  ];
  const shown = all ? rows : rows.slice(0, 12);
  return (
    <VisualFrame
      title="Memorization lifts (reference R vs holdout H)"
      concept="eval:lift"
      description={
        <span className="inline-flex flex-wrap items-center gap-0.5">
          Point and 95% interval on a log axis. The status gates on the interval&apos;s lower bound (ci_low), never on
          the point: a 3× lift with ci_low 0.03 is not evidence.
          <InfoHint concept="eval:gate-ci-bound" />
        </span>
      }
      data={rows.map((r) => ({
        metric: r.label,
        scope: r.sublabel,
        value: r.value,
        ci_low: r.lo,
        ci_high: r.hi ?? (r.lo !== null ? "∞" : null),
        status: r.status,
      }))}
      empty={{ when: rows.length === 0, message: "No lift metrics in this evaluation." }}
      footer={
        rows.length > 12 ? (
          <Button variant="ghost" size="sm" className="justify-self-start" onClick={() => setAll((v) => !v)}>
            {all ? "Show fewer" : `Show all ${rows.length} lifts`}
          </Button>
        ) : null
      }
    >
      <IntervalPlot
        rows={shown}
        scale={scale}
        lines={lines}
        ticks={LOG_TICKS.filter((t) => t >= scale.lo && t <= scale.hi)}
        formatTick={(t) => `${t}×`}
      />
    </VisualFrame>
  );
}

function HoldoutShare({ metrics, table }: { metrics: MetricRow[]; table?: string }) {
  const rows = metrics.filter(
    (m) => m.metric_id === "row.dcr_train_holdout_share" && (!table || m.table_name === table),
  );
  if (!rows.length) return null;
  const r0 = rows[0]!;
  const lines: RefLine[] = [
    { x: 0.5, label: "0.5 none", tone: "ref" },
    ...(r0.threshold_warn !== null
      ? [{ x: r0.threshold_warn, label: `warn ${fmtShare(r0.threshold_warn)}`, tone: "warn" as const }]
      : []),
    ...(r0.threshold_fail !== null
      ? [{ x: r0.threshold_fail, label: `fail ${fmtShare(r0.threshold_fail)}`, tone: "fail" as const }]
      : []),
  ];
  const interval: IntervalRow[] = rows.map((m) => ({
    key: m.table_name,
    label: m.table_name,
    sublabel: `n = ${formatCount(m.n_synthetic)} synthetic rows`,
    value: m.value,
    lo: m.ci_low,
    hi: m.ci_high,
    status: m.status,
    downgradedFrom: downgradedFrom(m),
    detail:
      m.status === "not_evaluated" || m.value === null
        ? `not evaluated — ${reasonOf(m) ?? "no reason"}`
        : `${fmtShare(m.value)} · Wilson ${fmtShare(m.ci_low)}–${fmtShare(m.ci_high)} · gate ${fmtShare(readingOf(m).gate)}`,
  }));
  const scale = { kind: "linear" as const, lo: 0.2, hi: 0.8 };
  return (
    <VisualFrame
      title="Closer-to-reference share (DCR holdout test)"
      concept="eval:holdout-share"
      description="Share of synthetic rows whose nearest record is in R rather than in H, with its Wilson 95% band; 0.5 = no preference. Gates on the band's lower bound."
      data={interval.map((r) => ({
        table: r.label,
        share: r.value,
        wilson_low: r.lo,
        wilson_high: r.hi,
        status: r.status,
      }))}
    >
      <IntervalPlot
        rows={interval}
        scale={scale}
        lines={lines}
        ticks={[0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]}
        formatTick={(t) => `${Math.round(t * 100)}%`}
      />
    </VisualFrame>
  );
}

function DistanceCharts({ rows, table, metrics }: { rows: ProfileRow[]; table: string; metrics: MetricRow[] }) {
  const tokens = useChartTokens();
  const get = (kind: "dcr_hist" | "nndr_hist", side: string) => {
    const row = rows.find((r) => r.table_name === table && r.profile_kind === kind && r.side === side);
    return row ? parseProfile(kind, row.payload) : null;
  };
  const dcr = useMemo(
    () =>
      pairedHistogram(
        { name: "syn → R", payload: get("dcr_hist", "synthetic") },
        { name: "H → R", payload: get("dcr_hist", "holdout") },
        tokens,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, table, tokens],
  );
  const nndr = useMemo(
    () =>
      pairedHistogram(
        { name: "syn → R", payload: get("nndr_hist", "synthetic") },
        { name: "H → R", payload: get("nndr_hist", "holdout") },
        tokens,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, table, tokens],
  );
  const dcrRatio = metrics.find((m) => m.metric_id === "row.dcr_p5_ratio" && m.table_name === table);
  const nndrRatio = metrics.find((m) => m.metric_id === "row.nndr_p5_ratio" && m.table_name === table);
  const p5 = (p: { p5: number | null; n: number } | null) =>
    p ? `p5 ${fmtSig(p.p5)}, n = ${formatCount(p.n)}` : MISSING;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="grid content-start gap-3">
        <ChartFrame
          title={`${table}: distance to closest record`}
          concept="eval:dcr"
          description={`Gower distance to the nearest reference row. syn→R ${p5(get("dcr_hist", "synthetic"))}; H→R ${p5(get("dcr_hist", "holdout"))}.`}
          option={dcr?.option ?? {}}
          data={dcr?.data ?? []}
          empty={{ when: !dcr, message: "DCR histograms not stored for this table." }}
          height={230}
        />
        {dcrRatio ? <MetricReadout row={dcrRatio} /> : null}
      </div>
      <div className="grid content-start gap-3">
        <ChartFrame
          title={`${table}: nearest-neighbour distance ratio`}
          concept="eval:nndr"
          description={`d₁ / d₂ to the reference set. syn→R ${p5(get("nndr_hist", "synthetic"))}; H→R ${p5(get("nndr_hist", "holdout"))}.`}
          option={nndr?.option ?? {}}
          data={nndr?.data ?? []}
          empty={{ when: !nndr, message: "NNDR histograms not stored for this table." }}
          height={230}
        />
        {nndrRatio ? <MetricReadout row={nndrRatio} /> : null}
      </div>
    </div>
  );
}

function keyText(value: unknown): string {
  if (value === null || value === undefined) return MISSING;
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(
        ([k, v]) =>
          `${k}=${typeof v === "object" && v !== null ? JSON.stringify(v) : String(v as string | number | boolean | null | undefined)}`,
      )
      .join(", ");
  }
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
    ? String(value)
    : JSON.stringify(value);
}

function FlaggedRows({ flags, table }: { flags: RowFlag[]; table?: string }) {
  const [all, setAll] = useState(false);
  const rows = flags.filter((f) => !table || f.table_name === table);
  const shown = all ? rows : rows.slice(0, 25);
  return (
    <section aria-labelledby="flags-title" className="grid gap-3">
      <h2 id="flags-title" className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1">
        Flagged rows
        <InfoHint concept="eval:row-flags" />
        <span className="ml-2 text-xs font-normal text-text-3">{rows.length} rows · keys only</span>
      </h2>
      {rows.length ? (
        <>
          <TableContainer aria-label="Flagged rows" className="max-h-[28rem]">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead scope="col">Check</TableHead>
                  <TableHead scope="col" className="text-right">
                    Rank
                  </TableHead>
                  <TableHead scope="col">Table</TableHead>
                  <TableHead scope="col">Synthetic key</TableHead>
                  <TableHead scope="col">Matched source (hash)</TableHead>
                  <TableHead scope="col">Set</TableHead>
                  <TableHead scope="col" className="text-right">
                    Distance
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {shown.map((f) => (
                  <TableRow key={`${f.table_name}-${f.check}-${f.rank}`}>
                    <TableCell className="text-xs whitespace-nowrap">{f.check.replaceAll("_", " ")}</TableCell>
                    <TableCell className="text-right text-xs tabular-nums">{f.rank}</TableCell>
                    <TableCell className="font-mono text-xs">{f.table_name}</TableCell>
                    <TableCell className="font-mono text-xs whitespace-nowrap">{keyText(f.synthetic_key)}</TableCell>
                    <TableCell
                      className="font-mono text-xs text-text-2"
                      title="keyed hash of the matched source record"
                    >
                      {f.source_key_hash ?? MISSING}
                    </TableCell>
                    <TableCell className="text-xs">{f.source_set ?? MISSING}</TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">{fmtSig(f.distance)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          {rows.length > 25 ? (
            <Button variant="ghost" size="sm" className="justify-self-start" onClick={() => setAll((v) => !v)}>
              {all ? "Show fewer" : `Show all ${rows.length} flagged rows`}
            </Button>
          ) : null}
        </>
      ) : (
        <p className="text-sm text-text-2">No rows were flagged{table ? ` in ${table}` : ""}.</p>
      )}
    </section>
  );
}

export function PrivacyPanel({
  evaluation,
  evaluationId,
  metrics,
  flags,
  table,
}: {
  evaluation: EvaluationRecord;
  evaluationId: string;
  metrics: MetricRow[];
  flags: RowFlag[];
  table?: string;
}) {
  const profiles = useProfiles(evaluationId, { table, kind: ["dcr_hist", "nndr_hist"] });
  const privacy = metrics.filter((m) => m.family === "privacy" && (!table || m.table_name === table));
  const rates = privacy
    .filter((m) => (PRIVACY_RATE_METRICS as readonly string[]).includes(m.metric_id))
    .sort((a, b) => statusRank(a.status) - statusRank(b.status))
    .slice(0, 6);
  const notEvaluated = privacy.filter((m) => m.status === "not_evaluated");
  const unverified = evaluation.tables.some((t) => t.reference_verified === false && (!table || t.name === table));
  const tables = [
    ...new Set(metrics.filter((m) => m.metric_id === "row.dcr_train_holdout_share").map((m) => m.table_name)),
  ]
    .filter((t) => !table || t === table)
    .sort();
  if (!privacy.length)
    return (
      <EmptyState
        title="No privacy metrics"
        description="This evaluation wrote no privacy-family rows (for example, it failed before the privacy stage)."
      />
    );
  return (
    <div className="grid gap-6">
      <Callout tone="info" title="Risk indicators, not guarantees">
        <span className="inline-flex flex-wrap items-center gap-0.5">
          Lifts, copy rates, DCR and NNDR measure how close synthetic rows sit to real ones. They flag memorization;
          they cannot prove privacy, which needs a formal mechanism such as differential privacy.
          <InfoHint concept="eval:privacy-risk" />
        </span>
      </Callout>
      {unverified || notEvaluated.length ? (
        <Callout tone={unverified ? "danger" : "warn"} title={`${notEvaluated.length} privacy metrics not evaluated`}>
          {[...new Set(notEvaluated.map((m) => reasonOf(m) ?? "no reason recorded"))].join(" · ")}. Not evaluated is not
          a pass.
        </Callout>
      ) : null}
      <Lifts metrics={privacy} table={table} />
      {rates.length ? (
        <section aria-labelledby="copy-rates" className="grid gap-3">
          <h2 id="copy-rates" className="text-lg font-semibold tracking-tight text-text-1">
            Copy rates
          </h2>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {rates.map((m) => (
              <MetricReadout key={`${m.metric_id}|${scopeLabel(m)}`} row={m} />
            ))}
          </div>
        </section>
      ) : null}
      <section aria-labelledby="dcr-title" className="grid gap-4">
        <h2 id="dcr-title" className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1">
          Distance to closest record <InfoHint concept="eval:dcr" />
        </h2>
        <HoldoutShare metrics={privacy} table={table} />
        {profiles.isPending ? (
          <Skeleton className="h-60 w-full" />
        ) : tables.length ? (
          tables.map((t) => <DistanceCharts key={t} rows={profiles.data?.data ?? []} table={t} metrics={privacy} />)
        ) : (
          <p className="text-sm text-text-2">No DCR test in this evaluation.</p>
        )}
        <p className="text-[11px] text-text-3">
          The DCR test ran on{" "}
          {fmtMetric(privacy.find((m) => m.metric_id === "row.dcr_train_holdout_share")?.n_synthetic ?? null, "count")}{" "}
          rows per side; the Wilson band says how much that n allows.
        </p>
      </section>
      <FlaggedRows flags={flags} table={table} />
    </div>
  );
}
