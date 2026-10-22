/**
 * ADR 0044 — the generation record of the evaluation's launch: the resolved generator config grouped by
 * section with where each value came from (file / flag / default), the params a config file overrode,
 * the launcher/worker version check, the relationships model the launch declared and its Dataflow job.
 */
import { Download } from "lucide-react";

import type { EvaluationRecord } from "@contracts/api";
import type { GenerationRecord } from "@contracts/record";

import { Callout } from "@/components/Callout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime, MISSING } from "@/lib/format";

import { configYaml } from "../lib/configYaml";
import { dataflowJobUrl, projectOf, SECTION_ORDER, shortVersion } from "../lib/record";

const PROVENANCE_VARIANT = { file: "accent", flag: "info", default: "neutral" } as const;

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** The text a snapshot field holds, or null for anything that is not a string. */
const text = (value: unknown): string | null => (typeof value === "string" ? value : null);

/** A value as text: strings as they are ("" shown as (empty)), anything else as JSON. */
function shown(value: unknown): string {
  if (value === "") return "(empty)";
  return typeof value === "string" ? value : (JSON.stringify(value) ?? String(value));
}

/** The pk columns and the count of fk edges of one table of the declared model (read defensively). */
function tableSummary(declaration: unknown): { pk: string; fks: number } {
  const object =
    typeof declaration === "object" && declaration !== null ? (declaration as Record<string, unknown>) : {};
  const pk = Array.isArray(object.pk) ? object.pk.filter((c): c is string => typeof c === "string") : [];
  const fk = object.fk;
  const fks = Array.isArray(fk) ? fk.length : typeof fk === "object" && fk !== null ? Object.keys(fk).length : 0;
  return { pk: pk.length ? pk.join(", ") : MISSING, fks };
}

function download(record: GenerationRecord) {
  const text = configYaml(record);
  if (text === null || typeof URL.createObjectURL !== "function") return;
  const url = URL.createObjectURL(new Blob([text], { type: "application/yaml" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `config.${record.snapshot_run_id ?? "run"}.yaml`;
  link.click();
  // After the click has started the download.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function GeneratorPanel({
  evaluation,
  record,
  error,
}: {
  evaluation: EvaluationRecord;
  /** Undefined while the record loads. */
  record: GenerationRecord | null | undefined;
  error?: string | null;
}) {
  if (error)
    return (
      <section aria-label="Generator">
        <p className="text-sm text-text-3">The generation record could not be read: {error}</p>
      </section>
    );
  if (record === undefined)
    return (
      <section aria-label="Generator" aria-busy="true">
        <Skeleton className="h-40 w-full" />
      </section>
    );
  const snapshot = record?.snapshot;
  // The snapshot's block types are never trusted (ADR 0044): a `values` that is not an object is no snapshot,
  // and any other block or entry of the wrong type is skipped.
  if (!record || !snapshot || !(snapshot.values === undefined || isObject(snapshot.values)))
    return (
      <section aria-label="Generator">
        <p className="text-sm text-text-2">
          No generation record: this run&apos;s validation_runs rows were written before ADR 0044 or into a table
          without the record columns. The registry columns below are all there is.
        </p>
      </section>
    );

  const values: Record<string, unknown> = snapshot.values ?? {};
  const blocks = new Map(Object.entries(values).filter((e): e is [string, Record<string, unknown>] => isObject(e[1])));
  const provenance: Record<string, unknown> = isObject(snapshot.provenance) ? snapshot.provenance : {};
  const overridden = (Array.isArray(snapshot.overridden) ? snapshot.overridden : []).filter(
    (o) => isObject(o) && typeof o.key === "string",
  );
  const sections = [
    ...SECTION_ORDER.filter((s) => blocks.has(s)),
    ...[...blocks.keys()].filter((s) => !SECTION_ORDER.includes(s)),
  ].filter((s) => Object.keys(blocks.get(s)!).length > 0);
  const jobs = [...new Map(record.runs.filter((r) => r.job_id).map((r) => [r.job_id!, r])).values()];
  const worker = record.worker_versions.join(", ");
  const launched = record.runs.find((r) => r.launched_at)?.launched_at ?? null;
  const model = isObject(snapshot.relationships) ? snapshot.relationships : null;
  // A batched launch's snapshot is batch 0's: the record's declared tables (ADR 0045) are the whole launch.
  const tables: [string, { pk: string; fks: number }][] = record.declared_tables.length
    ? record.declared_tables.map((t) => [t.name, { pk: t.pk.length ? t.pk.join(", ") : MISSING, fks: t.fk.length }])
    : (isObject(model?.tables) ? Object.entries(model.tables) : []).map(([name, declaration]) => [
        name,
        tableSummary(declaration),
      ]);
  const batchJobs = record.batches.length;

  return (
    <section aria-label="Generator" className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold tracking-tight text-text-1">Generator</h2>
        <span className="font-mono text-xs text-text-2" title={worker || record.launcher_version || undefined}>
          {shortVersion(record.worker_versions[0] ?? record.launcher_version)}
        </span>
        {record.version_mismatch ? (
          <Badge variant="outline" className="border-status-warn/60 text-status-warn-text">
            launcher {record.launcher_version ?? MISSING} ≠ worker {worker || MISSING}
          </Badge>
        ) : null}
        {record.config_digests.map((digest) => (
          <Badge key={digest} variant="neutral" className="font-mono">
            {digest}
          </Badge>
        ))}
        {launched ? <span className="text-xs text-text-3">launched {formatDateTime(launched)}</span> : null}
        {jobs.map((run) => (
          <a
            key={run.job_id}
            className="font-mono text-xs text-link underline-offset-4 hover:underline"
            href={dataflowJobUrl(run.job_id!, evaluation.generation_region, projectOf(run.landing_table))}
            target="_blank"
            rel="noreferrer noopener"
          >
            {run.job_id}
          </a>
        ))}
        <Button size="sm" className="ml-auto" disabled={configYaml(record) === null} onClick={() => download(record)}>
          <Download aria-hidden="true" />
          Download as config.yaml
        </Button>
      </div>
      {overridden.length ? (
        <Callout
          tone="warn"
          title={`The config file overrode ${overridden.length} typed param${overridden.length === 1 ? "" : "s"}`}
        >
          <ul className="grid gap-0.5">
            {overridden.map((o) => (
              <li key={o.key} className="font-mono text-xs">
                <code>{o.key}</code>: {shown(o.typed)} → {shown(o.file)}
              </li>
            ))}
          </ul>
        </Callout>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {sections.map((section) => (
          <Card key={section}>
            <CardHeader>
              <CardTitle>{section}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid">
                {Object.entries(blocks.get(section)!).map(([key, value]) => {
                  const source = text(provenance[`${section}.${key}`]);
                  return (
                    <div
                      key={key}
                      className="grid grid-cols-[minmax(6rem,10rem)_minmax(0,1fr)_auto] items-center gap-2 border-b border-border py-1 font-mono text-xs last:border-0"
                    >
                      <span className="text-text-2">{key}</span>
                      <span className="break-all text-text-1">{shown(value)}</span>
                      {source ? (
                        <Badge variant={PROVENANCE_VARIANT[source as keyof typeof PROVENANCE_VARIANT] ?? "outline"}>
                          {source}
                        </Badge>
                      ) : (
                        <span />
                      )}
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        ))}
        {model ? (
          <Card>
            <CardHeader>
              <CardTitle>Relationships model</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2 font-mono text-xs">
              <p className="text-text-1">
                {text(model.model) ?? MISSING} <span className="text-text-3">({text(model.sha12) ?? "no sha"})</span>
              </p>
              <p className="break-all text-text-2">{text(model.uri) ?? MISSING}</p>
              {batchJobs ? (
                <p className="text-text-1">
                  {tables.length} table{tables.length === 1 ? "" : "s"} · {batchJobs} batch job
                  {batchJobs === 1 ? "" : "s"}
                </p>
              ) : null}
              <ul className="grid">
                {tables.map(([name, { pk, fks }]) => {
                  return (
                    <li key={name} className="flex flex-wrap gap-2 border-b border-border py-1 last:border-0">
                      <span className="text-text-1">{name}</span>
                      <span className="text-text-2">pk {pk}</span>
                      <span className="text-text-3">
                        {fks} fk {fks === 1 ? "edge" : "edges"}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </CardContent>
          </Card>
        ) : null}
      </div>
    </section>
  );
}
