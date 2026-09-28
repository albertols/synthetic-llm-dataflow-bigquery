/**
 * The run header: the registry status and its reason, the scope of every
 * table (scope_ok / contaminated / expired / count mismatch / empty), the
 * reference check, sampled mode with its sample rates, versions and cost.
 */
import type { ReactNode } from "react";

import type { EvaluationRecord } from "@contracts/api";

import { Callout } from "@/components/Callout";
import { InfoHint } from "@/components/InfoHint";
import { StatusPill } from "@/components/StatusPill";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatBytes, formatCount, formatDateTime, formatDuration, formatPercent, MISSING } from "@/lib/format";

import { catalogueVersion } from "../lib/catalogue";

function Fact({ label, children, concept }: { label: string; children: ReactNode; concept?: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="flex items-center gap-0.5 text-[11px] text-text-3">
        {label}
        {concept ? <InfoHint concept={concept} /> : null}
      </dt>
      <dd className="truncate text-sm text-text-1">{children}</dd>
    </div>
  );
}

function ReferencePill({ verified }: { verified: boolean | null }) {
  if (verified === true) return <StatusPill status="reference_verified" tone="good" label="Verified" size="sm" />;
  if (verified === false)
    return <StatusPill status="reference_unverified" tone="critical" label="Not verified" size="sm" />;
  return <StatusPill status="reference_na" tone="neutral" label="n/a" size="sm" />;
}

function rate(value: number | null): string {
  return value === null ? MISSING : formatPercent(value, value < 0.01 ? 2 : 0);
}

export function RunHeader({
  evaluation,
  contractWarnings,
}: {
  evaluation: EvaluationRecord;
  contractWarnings: string[];
}) {
  const seconds =
    evaluation.finished_at && evaluation.evaluated_at
      ? (Date.parse(evaluation.finished_at) - Date.parse(evaluation.evaluated_at)) / 1000
      : null;
  const sampled = evaluation.mode === "sampled" || evaluation.tables.some((t) => t.sampled);
  const anyUnverified = evaluation.tables.some((t) => t.reference_verified === false);
  const badScope = evaluation.tables.filter((t) => t.scope_status && t.scope_status !== "ok");
  return (
    <section
      aria-labelledby="run-header-title"
      className="grid gap-4 rounded-lg border border-border bg-surface-1 p-4 md:p-5"
    >
      <h2 id="run-header-title" className="sr-only">
        Run status and scope
      </h2>
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill status={evaluation.status} />
        <span className="inline-flex items-center gap-0.5 text-xs text-text-3">
          {evaluation.event === "RUNNING" ? "running since" : "evaluated"} {formatDateTime(evaluation.evaluated_at)}
          {seconds !== null && seconds >= 0 ? ` · took ${formatDuration(seconds)}` : ""}
          <InfoHint concept="eval:evaluation" />
        </span>
        {sampled ? (
          <Badge variant="info">
            Sampled mode
            <InfoHint concept="eval:sampled-mode" />
          </Badge>
        ) : (
          <Badge variant="outline">Exact mode</Badge>
        )}
        {evaluation.catalogue_version !== catalogueVersion ? (
          <Badge variant="accent">
            Catalogue {evaluation.catalogue_version} (hints from {catalogueVersion})
            <InfoHint concept="eval:catalogue-version" />
          </Badge>
        ) : null}
      </div>
      {evaluation.status_reason ? <p className="text-sm text-text-2">{evaluation.status_reason}</p> : null}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
        <Fact label="Trigger · runner">
          {evaluation.trigger ?? MISSING} · {evaluation.runner ?? MISSING}
        </Fact>
        <Fact label="Evaluator · catalogue" concept="eval:catalogue-version">
          <span className="font-mono text-xs">
            {evaluation.evaluator_version} · {evaluation.catalogue_version}
          </span>
        </Fact>
        <Fact label="Environment">{evaluation.env ?? MISSING}</Fact>
        <Fact label="Launch">
          <span className="font-mono text-xs" title={evaluation.base_run_id ?? undefined}>
            {evaluation.base_run_id ?? MISSING}
          </span>
        </Fact>
        <Fact label="BigQuery bytes">{formatBytes(evaluation.bq_bytes_processed)}</Fact>
        <Fact label="Params from">{evaluation.params_source ?? MISSING}</Fact>
      </dl>

      {badScope.length ? (
        <Callout
          tone={badScope.some((t) => t.scope_status === "contaminated") ? "danger" : "warn"}
          title="Scope needs attention"
        >
          <ul className="grid gap-1">
            {badScope.map((t) => (
              <li key={t.name ?? t.landing_table}>
                <strong className="font-mono text-text-1">{t.name}</strong>: {t.scope_status}
                {t.scope_reason ? ` — ${t.scope_reason}` : ""}
              </li>
            ))}
          </ul>
        </Callout>
      ) : null}
      {anyUnverified ? (
        <Callout tone="danger" title="Reference not verified">
          The reference digest did not match the pinned source snapshot, so R and H are not the generator&apos;s own
          sets. Privacy lifts and the DCR holdout test are <strong>not evaluated</strong> — that is not a pass.
        </Callout>
      ) : null}
      {evaluation.warnings.length ? (
        <Callout
          tone="warn"
          title={`${evaluation.warnings.length} evaluator warning${evaluation.warnings.length === 1 ? "" : "s"}`}
        >
          <ul className="list-disc pl-4">
            {evaluation.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </Callout>
      ) : null}
      {contractWarnings.length ? (
        <Callout tone="info" title="Values newer than this GUI's contract">
          Shown as plain text: {contractWarnings.join("; ")}
        </Callout>
      ) : null}

      <TableContainer aria-label="Tables in scope">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead scope="col">Table</TableHead>
              <TableHead scope="col">Role</TableHead>
              <TableHead scope="col">
                <span className="inline-flex items-center gap-0.5">
                  Scope <InfoHint concept="eval:scope" />
                </span>
              </TableHead>
              <TableHead scope="col">
                <span className="inline-flex items-center gap-0.5">
                  Reference <InfoHint concept="eval:reference-verified" />
                </span>
              </TableHead>
              <TableHead scope="col" className="text-right">
                Rows synthetic / expected
              </TableHead>
              <TableHead scope="col" className="text-right">
                R / H / E
              </TableHead>
              <TableHead scope="col" className="text-right">
                <span className="inline-flex items-center gap-0.5">
                  Sample rate src / syn <InfoHint concept="eval:sampled-mode" />
                </span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {evaluation.tables.map((t) => (
              <TableRow key={`${t.name}-${t.run_id}`}>
                <TableCell className="font-mono text-xs">{t.name ?? MISSING}</TableCell>
                <TableCell className="text-xs text-text-2">{t.role ?? MISSING}</TableCell>
                <TableCell>
                  <StatusPill status={t.scope_status ?? "unknown"} size="sm" />
                </TableCell>
                <TableCell>
                  <ReferencePill verified={t.reference_verified} />
                </TableCell>
                <TableCell className="text-right text-xs whitespace-nowrap tabular-nums">
                  {formatCount(t.rows_synthetic)} / {formatCount(t.rows_expected)}
                </TableCell>
                <TableCell className="text-right text-xs whitespace-nowrap tabular-nums">
                  {formatCount(t.reference_n)} / {formatCount(t.holdout_n)} / {formatCount(t.exposure_n)}
                </TableCell>
                <TableCell className="text-right text-xs whitespace-nowrap tabular-nums">
                  {t.sampled ? `${rate(t.sample_rate_source)} / ${rate(t.sample_rate_synthetic)}` : "exact"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </section>
  );
}
