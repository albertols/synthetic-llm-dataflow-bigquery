/**
 * ADR 0044 — the generator config of the compared evaluations, as the generation record snapshotted it:
 * only the keys whose values differ, side by side, each with where the value came from (file / flag / default).
 */
import { configDiff, type GenerationRecord } from "@contracts/record";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { MISSING } from "@/lib/format";

const PROVENANCE_VARIANT = { file: "accent", flag: "info", default: "neutral" } as const;

/** A value as text: strings as they are ("" shown as (empty)), undefined as a dash, anything else as JSON. */
function shown(value: unknown): string {
  if (value === undefined) return MISSING;
  if (value === "") return "(empty)";
  return typeof value === "string" ? value : (JSON.stringify(value) ?? MISSING);
}

export function ConfigDiff({
  ids,
  records,
  errored = [],
}: {
  ids: readonly string[];
  records: readonly (GenerationRecord | null | undefined)[];
  /** Per id: the record request failed (as opposed to still loading). */
  errored?: readonly boolean[];
}) {
  const snapshots = records.map((r) => r?.snapshot ?? null);
  const rows = configDiff(snapshots);
  const withSnapshot = snapshots.filter((s) => s !== null).length;
  const loading = ids.filter((_, i) => records[i] === undefined && !errored[i]);
  const failed = ids.filter((_, i) => records[i] === undefined && errored[i]);
  const missing = ids.filter((_, i) => records[i] != null && snapshots[i] === null);
  const digests = records.map((r) => r?.config_digests ?? []);
  const sameDigest =
    ids.length > 0 && digests.every((d) => d.length === 1) && new Set(digests.map((d) => d[0])).size === 1
      ? digests[0]![0]
      : null;
  return (
    <section aria-label="Config diff">
      <Card>
        <CardHeader>
          <CardTitle>Generator config</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 text-xs">
          {loading.length ? <Skeleton className="h-6 w-full" /> : null}
          {failed.map((id) => (
            <p key={id} className="text-text-3">
              The generation record of {id} could not be read
            </p>
          ))}
          {missing.map((id) => (
            <p key={id} className="text-text-3">
              No generation record for {id} (ADR 0044)
            </p>
          ))}
          {withSnapshot >= 2 && rows.length === 0 ? (
            <p className="text-text-2">
              Same generator config
              {sameDigest ? <span className="font-mono text-text-3"> ({sameDigest})</span> : null}
            </p>
          ) : null}
          {rows.length ? (
            <TableContainer aria-label="Config keys that differ" className="max-h-[26rem]">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead scope="col">Key</TableHead>
                    {ids.map((id) => (
                      <TableHead key={id} scope="col">
                        <span className="font-mono">{id}</span>
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.key}>
                      <th scope="row" className="px-3 py-1 text-left font-mono text-xs font-normal text-text-1">
                        {row.key}
                      </th>
                      {ids.map((id, i) => {
                        const origin = row.provenance[i];
                        return (
                          <TableCell key={id} className="max-w-[16rem] font-mono text-xs text-text-2">
                            <span className="break-all">{shown(row.values[i])}</span>{" "}
                            {origin ? (
                              <Badge
                                variant={PROVENANCE_VARIANT[origin as keyof typeof PROVENANCE_VARIANT] ?? "outline"}
                              >
                                {origin}
                              </Badge>
                            ) : null}
                          </TableCell>
                        );
                      })}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          ) : null}
        </CardContent>
      </Card>
    </section>
  );
}
