/**
 * The source-stats explorer: `synthetic_rag.source_table_stats` per table and
 * column — entropy, deciles (with the DKW band on the sample tier), null and
 * empty rates, top values (literal policy), shape, lengths, temporal mixes
 * and the `__table__` null patterns — with tier badges, the profiler version,
 * and a side-by-side compare when one digest was profiled on both tiers.
 */
import { AlertTriangle, Database } from "lucide-react";
import type { SourceStats, SourceStatsColumn, SourceStatsSnapshot } from "@contracts/api";
import { dkwEpsilon } from "@synthetic-platform/stats";

import { Callout } from "@/components/Callout";
import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useFacets, useSourceStats } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatCount, formatDateTime, formatFixed, formatPercent, MISSING } from "@/lib/format";

import type { ConfigSearch } from "../route";
import { Part } from "../ui";
import { ColumnDetail } from "./ColumnDetail";
import { TierBadge } from "./TierBadge";
import {
  compareTiers,
  digestsWithBothTiers,
  isTableRow,
  shareInterval,
  shortDigest,
  type CompareRow,
  type Tier,
} from "./sourceModel";

type View = "latest" | "compare" | "snapshot";

export function SourceStatsExplorer({
  search,
  onSearch,
}: {
  search: ConfigSearch;
  onSearch: (patch: Partial<ConfigSearch>) => void;
}) {
  const facets = useFacets();
  const tables = facets.data?.data.source_tables ?? [];
  const table = search.table ?? tables[0]?.table_fqn;
  const view: View = search.snapshot ? "snapshot" : search.digest ? "compare" : "latest";
  const query = table
    ? {
        table,
        ...(view === "compare" && search.digest ? { digest: search.digest } : {}),
        ...(view === "snapshot" && search.snapshot ? { snapshot: [search.snapshot] } : {}),
      }
    : undefined;
  const stats = useSourceStats(query);
  const data = stats.data?.data;

  // A deep link names its table: render at once and let the table list arrive later.
  if (facets.isPending && !search.table)
    return (
      <div className="grid gap-3" role="status" aria-busy="true">
        <span className="sr-only">Loading source tables</span>
        <Skeleton className="h-10 w-1/2" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  if (facets.error)
    return (
      <Callout tone="danger" title="Source tables unavailable" live>
        {facets.error.message}. Check the BFF (npm start) and reload.
      </Callout>
    );
  if (!tables.length && !search.table)
    return (
      <EmptyState
        icon={Database}
        title="No source-table stats yet"
        description="Run a launch with --source_stats sample or exact to write synthetic_rag.source_table_stats."
      />
    );

  const bothTierDigests = data ? digestsWithBothTiers(data.snapshots) : [];

  return (
    <div className="grid gap-8">
      <Part
        id="src-pick"
        title="Source-table statistics"
        lead="What the profiler measured on the reference sample (sample tier) or on the whole table (exact tier). A digest can carry both, and several profiler versions: each snapshot is keyed by (digest, tier, profiler version, run)."
      >
        <div className="flex flex-wrap items-end gap-4" data-testid="sources-filters">
          <div className="grid gap-1">
            <label htmlFor="src-table" className="text-xs font-medium text-text-2">
              Table
            </label>
            <Select
              value={table}
              onValueChange={(v) => onSearch({ table: v, digest: undefined, snapshot: undefined, column: undefined })}
            >
              <SelectTrigger id="src-table" className="w-72 max-w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {tables.length === 0 && table ? <SelectItem value={table}>{table.split(".").pop()}</SelectItem> : null}
                {tables.map((t) => (
                  <SelectItem key={t.table_fqn} value={t.table_fqn}>
                    {t.table_fqn.split(".").pop()} · {t.tiers.join(" + ")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1">
            <span id="src-view" className="flex items-center gap-1 text-xs font-medium text-text-2">
              View <InfoHint concept="config:stats-tier" />
            </span>
            <ToggleGroup
              type="single"
              value={view}
              aria-labelledby="src-view"
              className="flex-wrap"
              onValueChange={(v) => {
                if (v === "latest") onSearch({ digest: undefined, snapshot: undefined });
                else if (v === "compare")
                  onSearch({ digest: bothTierDigests[0] ?? search.digest ?? "", snapshot: undefined });
                else if (v === "snapshot") onSearch({ snapshot: data?.snapshots[0]?.key, digest: undefined });
              }}
            >
              <ToggleGroupItem value="latest">Newest per tier</ToggleGroupItem>
              <ToggleGroupItem value="compare" disabled={!bothTierDigests.length && view !== "compare"}>
                Tier compare
              </ToggleGroupItem>
              <ToggleGroupItem value="snapshot">One snapshot</ToggleGroupItem>
            </ToggleGroup>
          </div>
          {view === "compare" ? (
            <div className="grid gap-1">
              <label htmlFor="src-digest" className="text-xs font-medium text-text-2">
                Digest profiled on both tiers
              </label>
              <Select value={search.digest} onValueChange={(v) => onSearch({ digest: v })}>
                <SelectTrigger id="src-digest" className="w-60 max-w-full font-mono">
                  <SelectValue placeholder="No digest has both tiers" />
                </SelectTrigger>
                <SelectContent>
                  {bothTierDigests.map((d) => (
                    <SelectItem key={d} value={d} className="font-mono">
                      {shortDigest(d)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
          {view === "snapshot" && data ? (
            <div className="grid gap-1">
              <label htmlFor="src-snapshot" className="text-xs font-medium text-text-2">
                Snapshot
              </label>
              <Select value={search.snapshot} onValueChange={(v) => onSearch({ snapshot: v })}>
                <SelectTrigger id="src-snapshot" className="w-80 max-w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {data.snapshots.map((s) => (
                    <SelectItem key={s.key} value={s.key}>
                      {s.computed_at.slice(0, 10)} · {s.legacy_tier ? "sample (NULL tier)" : s.tier} · v
                      {s.profiler_version ?? "?"} · {shortDigest(s.reference_digest)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
        </div>
      </Part>

      {stats.isPending && query ? (
        <div className="grid gap-3" role="status" aria-busy="true">
          <span className="sr-only">Loading source stats</span>
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : stats.error ? (
        <Callout tone="danger" title="Source stats unavailable" live>
          {stats.error.message}
        </Callout>
      ) : data ? (
        <StatsBody
          data={data}
          view={view}
          search={search}
          onSearch={onSearch}
          bytes={stats.data?.bytesEstimate ?? null}
        />
      ) : null}
    </div>
  );
}

function StatsBody({
  data,
  view,
  search,
  onSearch,
  bytes,
}: {
  data: SourceStats;
  view: View;
  search: ConfigSearch;
  onSearch: (patch: Partial<ConfigSearch>) => void;
  bytes: number | null;
}) {
  const selected = data.snapshots.filter((s) => data.selected.includes(s.key));
  const sample = selected.find((s) => s.tier === "sample");
  const exact = selected.find((s) => s.tier === "exact");
  const sameDigest = Boolean(sample && exact && sample.reference_digest === exact.reference_digest);
  const columns = data.columns;
  const names = [...new Set(columns.map((c) => c.column))].sort((a, b) =>
    isTableRow(a) ? 1 : isTableRow(b) ? -1 : a.localeCompare(b),
  );
  const column = search.column && names.includes(search.column) ? search.column : names.find((n) => !isTableRow(n));

  return (
    <div className="grid gap-8">
      <section aria-labelledby="src-snapshots" className="grid gap-3">
        <h2 id="src-snapshots" className="flex items-center gap-1 text-lg font-semibold text-text-1">
          Selected snapshots <InfoHint concept="config:snapshot" />
        </h2>
        <div className="grid gap-3 md:grid-cols-2" data-testid="selected-snapshots">
          {selected.map((s) => (
            <SnapshotCard key={s.key} snapshot={s} />
          ))}
        </div>
        {view === "latest" && sample && exact && !sameDigest ? (
          <Callout tone="info" title="Different reference samples">
            The newest sample-tier and exact-tier snapshots come from different digests, so their numbers differ for two
            reasons. Use Tier compare to see one digest on both tiers.
          </Callout>
        ) : null}
        {bytes !== null ? (
          <p className="text-xs text-text-3">BigQuery bytes for this view (dry run): {formatCount(bytes)} B</p>
        ) : null}
        <AllSnapshots snapshots={data.snapshots} selected={data.selected} />
      </section>

      {sample && exact && sameDigest ? (
        <TierCompare
          rows={compareTiers(columns, sample.key, exact.key, sample.sample_rows ?? 0)}
          sampleRows={sample.sample_rows ?? 0}
          onColumn={(c) => onSearch({ column: c })}
        />
      ) : null}

      <ColumnsTable columns={columns} snapshots={selected} active={column} onColumn={(c) => onSearch({ column: c })} />

      {column ? (
        <ColumnDetail
          key={column}
          column={column}
          rows={columns.filter((c) => c.column === column)}
          snapshots={selected}
          sameDigest={sameDigest}
        />
      ) : null}
    </div>
  );
}

function SnapshotCard({ snapshot: s }: { snapshot: SourceStatsSnapshot }) {
  return (
    <Card data-testid="snapshot-card" data-tier={s.tier}>
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          <TierBadge snapshot={s} />
          <span className="font-mono text-xs text-text-2">digest {shortDigest(s.reference_digest)}</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          <dt className="text-text-3">Rows profiled</dt>
          <dd className="font-mono text-text-1">
            {formatCount(s.sample_rows)} {s.tier === "exact" ? "(the whole table)" : "(the reference sample)"}
          </dd>
          <dt className="text-text-3">Profiler version</dt>
          <dd className="font-mono text-text-1">{s.profiler_version ?? "none (legacy)"}</dd>
          <dt className="text-text-3">Run</dt>
          <dd className="font-mono text-xs text-text-2 [overflow-wrap:anywhere]">{s.run_id}</dd>
          <dt className="text-text-3">Computed</dt>
          <dd className="text-text-2">{formatDateTime(s.computed_at)}</dd>
          <dt className="text-text-3">Columns</dt>
          <dd className="text-text-2">{formatCount(s.columns)}</dd>
          {s.tier === "sample" && s.sample_rows ? (
            <>
              <dt className="flex items-center gap-1 text-text-3">
                DKW band <InfoHint concept="stats:dkw" />
              </dt>
              <dd className="font-mono text-text-1">± {formatFixed(dkwEpsilon(s.sample_rows), 4)}</dd>
            </>
          ) : null}
        </dl>
      </CardContent>
    </Card>
  );
}

function AllSnapshots({ snapshots, selected }: { snapshots: SourceStatsSnapshot[]; selected: string[] }) {
  return (
    <details className="rounded-md border border-border bg-surface-1 px-3 py-2 text-sm">
      <summary className="cursor-pointer text-text-2">Every snapshot of this table ({snapshots.length})</summary>
      <TableContainer aria-label="Every stats snapshot of this table" className="mt-2">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead scope="col">Computed</TableHead>
              <TableHead scope="col">Tier</TableHead>
              <TableHead scope="col">Profiler</TableHead>
              <TableHead scope="col">Digest</TableHead>
              <TableHead scope="col" className="text-right">
                Rows
              </TableHead>
              <TableHead scope="col">Run</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {snapshots.map((s) => (
              <TableRow key={s.key} aria-current={selected.includes(s.key) ? "true" : undefined}>
                <TableCell className="whitespace-nowrap">{formatDateTime(s.computed_at)}</TableCell>
                <TableCell>
                  <TierBadge snapshot={s} />
                </TableCell>
                <TableCell className="font-mono">{s.profiler_version ?? MISSING}</TableCell>
                <TableCell className="font-mono">{shortDigest(s.reference_digest)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{formatCount(s.sample_rows)}</TableCell>
                <TableCell className="font-mono text-xs whitespace-nowrap">{s.run_id}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </details>
  );
}

function TierCompare({
  rows,
  sampleRows,
  onColumn,
}: {
  rows: CompareRow[];
  sampleRows: number;
  onColumn: (c: string) => void;
}) {
  const eps = sampleRows > 0 ? dkwEpsilon(sampleRows) : null;
  const truncated = rows.filter((r) => (r.truncation ?? 1) > 1.5);
  return (
    <section aria-labelledby="src-compare" className="grid gap-3" data-testid="tier-compare">
      <h2 id="src-compare" className="flex items-center gap-1 text-lg font-semibold text-text-1">
        One digest, both tiers <InfoHint concept="stats:distinct-truncation" />
      </h2>
      <p className="max-w-3xl text-sm text-text-2">
        The same reference sample profiled twice: fractions agree within the DKW band (±
        {eps === null ? MISSING : formatFixed(eps, 4)} at n = {formatCount(sampleRows)}), distinct counts do not —{" "}
        {truncated.length} of {rows.length} columns saw fewer than two thirds of their true distinct values in the
        sample.
      </p>
      <TableContainer aria-label="Sample tier vs exact tier, per column">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead scope="col">Column</TableHead>
              <TableHead scope="col" className="text-right">
                Distinct (sample)
              </TableHead>
              <TableHead scope="col" className="text-right">
                Distinct (exact)
              </TableHead>
              <TableHead scope="col" className="text-right">
                Exact ÷ sample
              </TableHead>
              <TableHead scope="col" className="text-right">
                Null (sample)
              </TableHead>
              <TableHead scope="col" className="text-right">
                Null (exact)
              </TableHead>
              <TableHead scope="col">Null gap vs band</TableHead>
              <TableHead scope="col" className="text-right">
                Entropy S / E (bits)
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.column}>
                <TableCell>
                  <button
                    type="button"
                    onClick={() => onColumn(r.column)}
                    className="cursor-pointer font-mono text-link hover:underline"
                  >
                    {r.column}
                  </button>
                </TableCell>
                <TableCell className="text-right font-mono tabular-nums">{formatCount(r.sampleDistinct)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{formatCount(r.exactDistinct)}</TableCell>
                <TableCell
                  className={cn(
                    "text-right font-mono tabular-nums",
                    (r.truncation ?? 1) > 1.5 && "font-semibold text-status-warn-text",
                  )}
                >
                  {r.truncation === null ? (
                    MISSING
                  ) : (r.truncation ?? 1) > 1.5 ? (
                    <span className="inline-flex items-center gap-1">
                      <AlertTriangle className="size-3.5" aria-hidden="true" />
                      {formatFixed(r.truncation, 1)}×<span className="sr-only"> (sample truncated the count)</span>
                    </span>
                  ) : (
                    `${formatFixed(r.truncation, 1)}×`
                  )}
                </TableCell>
                <TableCell className="text-right font-mono tabular-nums">{formatPercent(r.sampleNull, 2)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{formatPercent(r.exactNull, 2)}</TableCell>
                <TableCell>
                  {r.nullWithinBand === null ? (
                    MISSING
                  ) : r.nullWithinBand ? (
                    <span className="text-text-2">within ±ε</span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-status-warn-text">
                      <AlertTriangle className="size-3.5" aria-hidden="true" /> outside ±ε
                    </span>
                  )}
                </TableCell>
                <TableCell className="text-right font-mono whitespace-nowrap tabular-nums">
                  {formatFixed(r.sampleEntropy, 2)} / {formatFixed(r.exactEntropy, 2)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </section>
  );
}

function ColumnsTable({
  columns,
  snapshots,
  active,
  onColumn,
}: {
  columns: SourceStatsColumn[];
  snapshots: SourceStatsSnapshot[];
  active: string | undefined;
  onColumn: (c: string) => void;
}) {
  const byKey = new Map(snapshots.map((s) => [s.key, s]));
  const rows = [...columns].sort(
    (a, b) =>
      (isTableRow(a.column) ? 1 : 0) - (isTableRow(b.column) ? 1 : 0) ||
      a.column.localeCompare(b.column) ||
      a.tier.localeCompare(b.tier),
  );
  return (
    <section aria-labelledby="src-columns" className="grid gap-3">
      <h2 id="src-columns" className="text-lg font-semibold text-text-1">
        Columns
      </h2>
      <p className="text-sm text-text-2">
        Pick a column for its deciles, top values, shapes and mixes. Null rates on the sample tier carry a 95 % Wilson
        interval; a sample-tier distinct can never exceed the rows the sample saw.
      </p>
      <TableContainer aria-label="Per-column stats of the selected snapshots" className="max-h-[32rem]">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead scope="col">Column</TableHead>
              <TableHead scope="col">Plan</TableHead>
              <TableHead scope="col">Tier</TableHead>
              <TableHead scope="col" className="text-right">
                <span className="inline-flex items-center gap-1">
                  Null % <InfoHint concept="stats:null-rate" />
                </span>
              </TableHead>
              <TableHead scope="col" className="text-right">
                Empty %
              </TableHead>
              <TableHead scope="col" className="text-right">
                Distinct
              </TableHead>
              <TableHead scope="col" className="text-right">
                <span className="inline-flex items-center gap-1">
                  Entropy <InfoHint concept="stats:entropy" />
                </span>
              </TableHead>
              <TableHead scope="col" className="text-right">
                <span className="inline-flex items-center gap-1">
                  H norm <InfoHint concept="stats:entropy-norm" />
                </span>
              </TableHead>
              <TableHead scope="col" className="text-right">
                Top-1
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((c) => {
              const snap = byKey.get(c.snapshot_key);
              const tier: Tier = c.tier;
              const ci = shareInterval(c.null_fraction, c.sample_rows, tier);
              const st = c.stats_parsed;
              const capped =
                tier === "sample" && c.sample_rows && c.distinct !== null && c.distinct >= c.sample_rows * 0.5;
              return (
                <TableRow
                  key={`${c.snapshot_key}-${c.column}`}
                  data-state={c.column === active ? "selected" : undefined}
                  className={c.column === active ? "bg-accent-soft" : undefined}
                >
                  <TableCell>
                    <button
                      type="button"
                      onClick={() => onColumn(c.column)}
                      aria-pressed={c.column === active}
                      className="cursor-pointer font-mono text-link hover:underline"
                    >
                      {c.column}
                    </button>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-text-2">{c.generation_plan ?? MISSING}</TableCell>
                  <TableCell>{snap ? <TierBadge snapshot={snap} /> : c.tier}</TableCell>
                  <TableCell className="text-right font-mono whitespace-nowrap tabular-nums">
                    {ci ? formatPercent(ci.share, 2) : MISSING}
                    {ci && ci.low !== null && ci.high !== null ? (
                      <span className="block text-[11px] text-text-3">
                        [{formatPercent(ci.low, 2)} – {formatPercent(ci.high, 2)}]
                      </span>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {formatPercent(c.empty_fraction, 2)}
                  </TableCell>
                  <TableCell className="text-right font-mono whitespace-nowrap tabular-nums">
                    {formatCount(c.distinct)}
                    {capped ? (
                      <span className="block text-[11px] text-status-warn-text">near the sample row cap</span>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {formatFixed(st?.entropy ?? null, 2)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {formatFixed(st?.entropy_norm ?? null, 3)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {formatPercent(st?.top1_share ?? null, 1)}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
    </section>
  );
}
