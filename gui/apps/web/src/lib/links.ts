/** Outbound links shared by the shell, SourceLink and the concept files. */

export const REPO_URL = "https://github.com/albertols/synthetic-llm-dataflow-bigquery";
export const DSG_URL = "https://github.com/GoogleCloudPlatform/dataflow-solution-guides";
/** The ref code links point at; the GUI documents the default branch. */
export const REPO_REF = "master";

/** A GitHub blob URL for a repo-relative `path`, optionally anchored at `line`. */
export function repoBlobUrl(path: string, line?: number, ref: string = REPO_REF): string {
  const clean = path.replace(/^\.?\/+/, "");
  const anchor = line && line > 0 ? `#L${line}` : "";
  return `${REPO_URL}/blob/${ref}/${clean}${anchor}`;
}

/** Splits a knob-style `source` ("packages/a/b.py:42") into a path and a line. */
export function parseSource(source: string): { path: string; line?: number } {
  const match = /^(.*?):(\d+)$/.exec(source.trim());
  if (!match?.[1] || !match[2]) return { path: source.trim() };
  return { path: match[1], line: Number(match[2]) };
}
