/**
 * Relational: per foreign-key edge, the orphan rate (enforced edges gate at
 * 0; documented edges are INFO next to the source's own orphan rate, never a
 * FAIL), the fan-out histograms overlaid, the childless-parent share and the
 * edge's other metrics.
 */
import { useMemo } from "react";

import type { MetricRow, ProfileRow } from "@contracts/api";
import { parseProfile } from "@contracts/payloads";

import { ChartFrame } from "@/components/ChartFrame";
import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { StatusPill } from "@/components/StatusPill";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useProfiles } from "@/lib/api";
import { formatCount, MISSING } from "@/lib/format";

import { MetricReadout } from "../components/MetricReadout";
import { fanoutChart } from "../lib/charts";
import { fmtShare } from "../lib/format";
import type { Graph, GraphEdge } from "../lib/graph";
import { statusRank } from "../lib/model";
import { isDocumentedEdge } from "../lib/reading";
import { useChartTokens } from "../lib/tokens";

function EdgeDetail({ edge, profiles }: { edge: GraphEdge; profiles: ProfileRow[] }) {
  const tokens = useChartTokens();
  const [source, synthetic] = useMemo(() => {
    const get = (side: string) => {
      const row = profiles.find((p) => p.edge === edge.label && p.profile_kind === "fanout_hist" && p.side === side);
      return row ? parseProfile("fanout_hist", row.payload) : null;
    };
    return [get("source"), get("synthetic")] as const;
  }, [profiles, edge.label]);
  const spec = useMemo(() => fanoutChart(source, synthetic, tokens), [source, synthetic, tokens]);
  const others = edge.rows
    .filter((r) => r.metric_id !== "relationship.orphan_rate" && r.metric_id !== "relationship.orphan_rate_source")
    .sort((a, b) => statusRank(a.status) - statusRank(b.status));
  return (
    <section
      aria-labelledby={`edge-${edge.label}`}
      className="grid gap-3 rounded-lg border border-border bg-surface-1 p-4"
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 id={`edge-${edge.label}`} className="font-mono text-sm font-semibold break-all text-text-1">
          {edge.label}
        </h3>
        <Badge variant={edge.documented ? "outline" : "neutral"}>
          {edge.documented ? "documented" : (edge.role ?? "enforced")}
        </Badge>
        {edge.status ? <StatusPill status={edge.status} size="sm" /> : null}
      </header>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <ChartFrame
          title="Fan-out: children per parent"
          concept="eval:fanout"
          description={
            source && synthetic
              ? `Mean ${source.mean.toFixed(2)} → ${synthetic.mean.toFixed(2)} children; childless parents ${fmtShare(source.zero_child_share)} → ${fmtShare(synthetic.zero_child_share)}.`
              : undefined
          }
          option={spec?.option ?? {}}
          data={spec?.data ?? []}
          empty={{ when: !spec, message: "No fan-out histogram stored for this edge." }}
          height={240}
        />
        <div className="grid content-start gap-3">
          {others.length ? (
            others.map((row) => <MetricReadout key={row.metric_id} row={row} showScope={false} />)
          ) : (
            <p className="text-sm text-text-3">No other metrics on this edge.</p>
          )}
        </div>
      </div>
    </section>
  );
}

export function RelationalPanel({
  evaluationId,
  graph,
  table,
}: {
  evaluationId: string;
  graph: Graph;
  table?: string;
}) {
  const profiles = useProfiles(evaluationId, { table, kind: ["fanout_hist"] });
  const edges = graph.edges.filter((e) => e.rows.length && (!table || e.child === table || e.parent === table));
  if (!edges.length)
    return (
      <EmptyState
        title="No relationship metrics"
        description="The launch generated one table, or its relationship model declares no foreign keys between generated tables."
      />
    );
  const orphanRow = (row: MetricRow | null) => (row ? row.value : null);
  return (
    <div className="grid gap-6">
      <section aria-labelledby="orphans-title" className="grid gap-3">
        <h2 id="orphans-title" className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1">
          Orphan rates
          <InfoHint concept="metric:relationship.orphan_rate" />
        </h2>
        <p className="flex flex-wrap items-center gap-0.5 text-sm text-text-2">
          Enforced edges are generated from their parent&apos;s keys, so any orphan fails. Documented edges (enforced:
          false) are reported as INFO next to the source&apos;s own orphan rate — never as a FAIL.
          <InfoHint concept="eval:documented-edge" />
        </p>
        <TableContainer aria-label="Orphan rate per foreign key">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead scope="col">Edge (child → parent)</TableHead>
                <TableHead scope="col">Enforcement</TableHead>
                <TableHead scope="col" className="text-right">
                  Synthetic orphan rate
                </TableHead>
                <TableHead scope="col" className="text-right">
                  Orphans
                </TableHead>
                <TableHead scope="col" className="text-right">
                  <span className="inline-flex items-center gap-0.5">
                    Source orphan rate <InfoHint concept="metric:relationship.orphan_rate_source" />
                  </span>
                </TableHead>
                <TableHead scope="col">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {edges.map((edge) => {
                const orphans = (edge.orphan?.detail as { orphans?: number } | null | undefined)?.orphans;
                const documented = edge.documented || (edge.orphan ? isDocumentedEdge(edge.orphan) : false);
                return (
                  <TableRow key={edge.label} data-edge={edge.label}>
                    <TableCell className="font-mono text-xs break-all">{edge.label}</TableCell>
                    <TableCell className="text-xs whitespace-nowrap">
                      {documented ? "documented (enforced: false)" : `enforced${edge.role ? ` · ${edge.role}` : ""}`}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">
                      {fmtShare(orphanRow(edge.orphan))}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">
                      {orphans === undefined ? MISSING : formatCount(orphans)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">
                      {fmtShare(orphanRow(edge.orphanSource))}
                    </TableCell>
                    <TableCell>
                      {edge.orphan ? (
                        <StatusPill
                          status={documented ? "info" : edge.orphan.status}
                          size="sm"
                          label={documented ? "Info · documented" : undefined}
                        />
                      ) : (
                        <span className="text-xs text-text-3">{MISSING}</span>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      </section>
      <section aria-labelledby="fanout-title" className="grid gap-4">
        <h2 id="fanout-title" className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1">
          Fan-out per edge <InfoHint concept="eval:fanout" />
        </h2>
        {profiles.isPending ? (
          <Skeleton className="h-60 w-full" />
        ) : (
          edges.map((edge) => <EdgeDetail key={edge.label} edge={edge} profiles={profiles.data?.data ?? []} />)
        )}
      </section>
    </div>
  );
}
