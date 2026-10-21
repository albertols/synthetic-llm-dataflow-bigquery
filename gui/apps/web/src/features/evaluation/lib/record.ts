/** Links and labels for the generation record (ADR 0044). */
import { parseGeneratorVersion } from "@contracts/record";

import { MISSING } from "@/lib/format";

export function dataflowJobUrl(jobId: string, region: string | null, project: string | null): string {
  const where = region ? `${encodeURIComponent(region)}/` : "";
  const query = project ? `?project=${encodeURIComponent(project)}` : "";
  return `https://console.cloud.google.com/dataflow/jobs/${where}${encodeURIComponent(jobId)}${query}`;
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
