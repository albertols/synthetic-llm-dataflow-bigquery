/** Links and labels for the generation record (ADR 0044). */
import { parseGeneratorVersion, type GenerationRecord } from "@contracts/record";

import { REPEAT_SHARE_TOLERANCE } from "@/lib/adjustments";
import { MISSING } from "@/lib/format";

export function dataflowJobUrl(jobId: string, region: string | null, project: string | null): string {
  const where = region ? `${encodeURIComponent(region)}/` : "";
  const query = project ? `?project=${encodeURIComponent(project)}` : "";
  return `https://console.cloud.google.com/dataflow/jobs/${where}${encodeURIComponent(jobId)}${query}`;
}

/**
 * The region a job link needs: the registry's `generation_region`, else the region the launch recorded in its
 * snapshot's `beam` block — an evaluation by `--run_id` (every whole-model launch's, ADR 0045) records none.
 * Read defensively, as every snapshot block (ADR 0044): a `beam` that is not an object, or a region that is
 * not a non-empty string, is no region, and a record written before `beam.region` has none. Null: render the
 * job id as text, never a link without its region.
 */
export function generationRegion(
  registry: string | null | undefined,
  record: Pick<GenerationRecord, "snapshot"> | null | undefined,
): string | null {
  if (registry) return registry;
  const beam: unknown = record?.snapshot?.beam;
  if (typeof beam !== "object" || beam === null || Array.isArray(beam)) return null;
  const region = (beam as Record<string, unknown>).region;
  return typeof region === "string" && region !== "" ? region : null;
}

/** The project of `project.dataset.table` (or `project:dataset.table`); null for a shorter name. */
export function projectOf(fqn: string | null | undefined): string | null {
  if (!fqn) return null;
  const parts = fqn.replace(":", ".").split(".");
  return parts.length === 3 ? parts[0]! : null;
}

/** "0.6.1 · edcfdcd" (a trailing * when dirty); the raw text when it is not a generator version. */
export function shortVersion(version: string | null): string {
  if (!version) return MISSING;
  const parsed = parseGeneratorVersion(version);
  return parsed ? `${parsed.base} · ${parsed.sha}${parsed.dirty ? "*" : ""}` : version;
}

/** The generator's sections, in the order the reference file lists them. */
export const SECTION_ORDER = [
  "io",
  "launch",
  "sampling",
  "generation",
  "free_text",
  "rag",
  "guardrails",
  "relational",
  "serving",
];

/** The run's `guardrails.repeat_share_tolerance` (A1 ruling R16), else the default. */
export function toleranceOf(record: GenerationRecord | null | undefined): number {
  const value = record?.snapshot?.values?.guardrails?.repeat_share_tolerance;
  return typeof value === "number" ? value : REPEAT_SHARE_TOLERANCE;
}
