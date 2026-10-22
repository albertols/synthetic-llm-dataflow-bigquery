/**
 * The model summary card (ADR 0036, ADR 0044, ADR 0045): what the launch declared, in counts: its
 * tables, how many declare a primary key, the foreign keys between them, the isolated ones, the tables
 * read from another source and the batch jobs a whole-model launch was split into, each linked to its
 * Dataflow job — or, when no region is known (lib/record.ts `generationRegion`), its job id as text. The
 * counts come from the generation record when the run has one, else from the registry and the metric
 * rows; the card says which.
 */
import { InfoHint } from "@/components/InfoHint";
import { Badge } from "@/components/ui/badge";
import { formatCount, MISSING } from "@/lib/format";

import { dataflowJobUrl } from "../lib/record";
import { batchLabel, type ModelSummary } from "../lib/tables";

function Stat({ label, value, note }: { label: string; value: string; note?: string | null }) {
  return (
    <div data-stat={label} className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-[11px] text-text-3">{label}</dt>
      <dd className="font-mono text-sm text-text-1 tabular-nums">{value}</dd>
      {note ? <dd className="text-[11px] text-text-3">{note}</dd> : null}
    </div>
  );
}

export function ModelSummaryCard({
  summary,
  region,
  project,
}: {
  summary: ModelSummary;
  /** The generation's Dataflow region (null: unknown, the job ids show as text) and the landing tables' project. */
  region: string | null;
  project: string | null;
}) {
  const batchNote =
    summary.batchCount === null
      ? null
      : summary.batches.length < summary.batchCount
        ? `${formatCount(summary.batches.length)} recorded`
        : summary.batchCount === 1
          ? batchLabel(0)
          : `${batchLabel(0)} … ${batchLabel(summary.batchCount - 1)}`;
  return (
    <section aria-label="Model summary" className="grid gap-3 rounded-lg border border-border bg-surface-1 p-4">
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h2 className="flex items-center gap-0.5 text-sm font-semibold text-text-1">
          Model
          <InfoHint concept="eval:model-summary" />
        </h2>
        <span className="font-mono text-sm text-text-1">{summary.model ?? MISSING}</span>
        {summary.sha12 ? <Badge variant="outline">sha12 {summary.sha12}</Badge> : null}
        <span className="text-xs text-text-3">
          {summary.source === "record"
            ? "from the generation record (ADR 0044)"
            : "from the registry and the metric rows (no generation record)"}
        </span>
      </header>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Tables"
          value={formatCount(summary.tables)}
          note={
            summary.declaredTotal !== null
              ? `of ${formatCount(summary.declaredTotal)} declared · ${formatCount(summary.disabled)} disabled`
              : "disabled: not recorded"
          }
        />
        <Stat
          label="Primary key"
          value={`${formatCount(summary.withPk)} declared`}
          note={`${formatCount(summary.withoutPk)} none${summary.pkUnknown ? ` · ${formatCount(summary.pkUnknown)} unknown` : ""}`}
        />
        <Stat label="Edges" value={formatCount(summary.edges)} />
        <Stat label="Isolated" value={formatCount(summary.isolated)} />
        <Stat
          label="Source overrides"
          value={summary.sourceOverrides === null ? MISSING : formatCount(summary.sourceOverrides)}
        />
        <Stat
          label="Batch jobs"
          value={summary.batchCount === null ? MISSING : formatCount(summary.batchCount)}
          note={batchNote}
        />
      </dl>
      {summary.batches.length ? (
        <ul aria-label="Batch jobs" className="m-0 flex list-none flex-wrap gap-x-3 gap-y-1 p-0 text-xs">
          {summary.batches.map((b) => (
            <li key={b.index} className="flex gap-x-1.5">
              {b.job_ids.length ? (
                b.job_ids.map((jobId, k) => {
                  const label = `${batchLabel(b.index)}${k > 0 ? ` (${k + 1})` : ""}`;
                  // No region, no link: a console URL without its region does not open the job.
                  return region ? (
                    <a
                      key={jobId}
                      href={dataflowJobUrl(jobId, region, project)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex min-h-6 min-w-6 items-center font-mono text-link underline underline-offset-2"
                    >
                      {label} ↗<span className="sr-only"> (Dataflow job {jobId}, opens in a new tab)</span>
                    </a>
                  ) : (
                    <span key={jobId} className="inline-flex min-h-6 min-w-0 flex-wrap items-center gap-x-1">
                      <span className="font-mono text-text-2">{label}</span>{" "}
                      <span className="font-mono [overflow-wrap:anywhere] text-text-3">{jobId}</span>
                    </span>
                  );
                })
              ) : (
                <span className="font-mono text-text-3">{batchLabel(b.index)}</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
