/**
 * Detection: can a classifier tell synthetic rows from source rows? Per
 * table the ROC curve with the AUC interval drawn as a band (binormal curves
 * at the interval's bounds), the detection AUC readout (noise floor = half
 * the DeLong interval) and the pMSE ratio.
 */
import { useMemo } from "react";

import type { MetricRow, ProfileRow } from "@contracts/api";
import { parseProfile } from "@contracts/payloads";

import { ChartFrame } from "@/components/ChartFrame";
import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfiles } from "@/lib/api";
import { formatCount } from "@/lib/format";

import { MetricReadout } from "../components/MetricReadout";
import { rocChart } from "../lib/charts";
import { fmtSig } from "../lib/format";
import { useChartTokens } from "../lib/tokens";

function TableDetection({
  table,
  auc,
  pmse,
  profiles,
}: {
  table: string;
  auc?: MetricRow;
  pmse?: MetricRow;
  profiles: ProfileRow[];
}) {
  const tokens = useChartTokens();
  const roc = useMemo(() => {
    const row = profiles.find((p) => p.table_name === table && p.profile_kind === "roc_curve");
    return row ? parseProfile("roc_curve", row.payload) : null;
  }, [profiles, table]);
  const spec = useMemo(
    () => rocChart(roc, { low: auc?.ci_low ?? null, high: auc?.ci_high ?? null }, tokens),
    [roc, auc, tokens],
  );
  return (
    <section aria-labelledby={`detect-${table}`} className="grid gap-3">
      <h3 id={`detect-${table}`} className="font-mono text-sm font-semibold text-text-1">
        {table}
      </h3>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <ChartFrame
          title={`${table}: detection ROC`}
          concept="eval:detection"
          description={
            roc
              ? `AUC ${fmtSig(roc.auc)} (95% ${fmtSig(auc?.ci_low ?? roc.ci_low)}–${fmtSig(auc?.ci_high ?? roc.ci_high)}) on ${formatCount(roc.n_source)} source and ${formatCount(roc.n_synthetic)} synthetic rows; the diagonal is chance.`
              : undefined
          }
          option={spec?.option ?? {}}
          data={spec?.data ?? []}
          empty={{ when: !spec, message: "No ROC curve stored for this table." }}
          height={300}
        />
        <div className="grid content-start gap-3">
          {auc ? <MetricReadout row={auc} /> : null}
          {pmse ? <MetricReadout row={pmse} /> : null}
        </div>
      </div>
    </section>
  );
}

export function DetectionPanel({
  evaluationId,
  metrics,
  table,
}: {
  evaluationId: string;
  metrics: MetricRow[];
  table?: string;
}) {
  const profiles = useProfiles(evaluationId, { table, kind: ["roc_curve"] });
  const rows = metrics.filter(
    (m) =>
      (m.metric_id === "table.detection_auc" || m.metric_id === "table.pmse_ratio") &&
      (!table || m.table_name === table),
  );
  const tables = [...new Set(rows.map((m) => m.table_name))].sort();
  if (!tables.length)
    return (
      <EmptyState title="No detection metrics" description="The detection classifier did not run in this evaluation." />
    );
  return (
    <div className="grid gap-6">
      <p className="flex max-w-4xl flex-wrap items-center gap-0.5 text-sm text-text-2">
        A classifier is trained to separate synthetic from source rows. AUC 0.5 means it cannot; the band is the AUC
        interval as binormal ROC curves, and the AUC&apos;s noise floor is half that interval.
        <InfoHint concept="eval:detection" />
      </p>
      {profiles.isPending ? (
        <Skeleton className="h-72 w-full" />
      ) : (
        tables.map((t) => (
          <TableDetection
            key={t}
            table={t}
            auc={rows.find((m) => m.table_name === t && m.metric_id === "table.detection_auc")}
            pmse={rows.find((m) => m.table_name === t && m.metric_id === "table.pmse_ratio")}
            profiles={profiles.data?.data ?? []}
          />
        ))
      )}
    </div>
  );
}
