/**
 * Where every CONFIG link points. Code links resolve at the commit the knob
 * export ran on (`knobs.json` `exported_from`), so `path:LINE` always lands on
 * the line the exporter verified; a dirty export (uncommitted edits) falls
 * back to the default branch. ADR and doc links use the same ref.
 */
import { ADRS } from "@contracts/concepts/config";
import { knobs } from "@contracts/generated/knobs";

import { parseSource, repoBlobUrl, REPO_REF } from "@/lib/links";

/** The git ref every CONFIG link resolves at. */
export const CODE_REF: string =
  knobs.exported_from.commit && knobs.exported_from.dirty.length === 0 ? knobs.exported_from.commit : REPO_REF;

/** Short form for display ("dbda238"). */
export const CODE_REF_SHORT = CODE_REF.length === 40 ? CODE_REF.slice(0, 7) : CODE_REF;

/** `path:LINE` (knobs.json `source`) → GitHub blob URL at CODE_REF. */
export function sourceUrl(source: string): string {
  const { path, line } = parseSource(source);
  return repoBlobUrl(path, line, CODE_REF);
}

/** A repo-relative doc path → GitHub blob URL at CODE_REF. */
export function docUrl(path: string, line?: number): string {
  return repoBlobUrl(path, line, CODE_REF);
}

export { ADRS } from "@contracts/concepts/config";

/** ADR number ("0022") → GitHub URL, or null when the map does not know it. */
export function adrUrl(number: string): string | null {
  const adr = ADRS[number];
  return adr ? docUrl(`docs/adr/${adr.file}`) : null;
}

/** "docs/designs/2026-07-24-reference-sample-scaling.md" → "Reference sample scaling (2026-07-24)". */
export function docTitle(path: string): string {
  const file = path.split("/").pop() ?? path;
  const match = /^(\d{4}-\d{2}-\d{2})-(.+)\.md$/.exec(file);
  if (!match?.[1] || !match[2]) return file.replace(/\.md$/, "");
  const words = match[2].replace(/-/g, " ");
  return `${words.charAt(0).toUpperCase()}${words.slice(1)} (${match[1]})`;
}

/** Content sources this tab reuses (never retyped numbers; see the report). */
export const DOCS = {
  scaling: "docs/designs/2026-07-24-reference-sample-scaling.md",
  sourceStats: "docs/designs/2026-08-05-source-table-stats.md",
  article2: "docs/articles/02-type-system-freetext-resolution.md",
  articles: "docs/articles/README.md",
  throughput: "docs/designs/2026-09-07-generation-throughput-where-time-goes.md",
  ws5: "docs/designs/2026-07-26-ws5-generation-throughput.md",
  expansion: "docs/designs/2026-08-05-freetext-expansion-modes.md",
  readme: "README.md",
  article1: "docs/articles/01-building-banking-synthetic-data-intro.md",
} as const;
