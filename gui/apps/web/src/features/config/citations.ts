/**
 * Every `path:line` this tab types by hand (the knob sources come from
 * knobs.json, verified by the exporter). Each carries the text found on that
 * line at the exported commit; `citations.test.ts` reads the worktree files
 * and fails when a line moves, so a stale link cannot ship.
 */
export type Citation = { source: string; excerpt: string };

const PIPELINE = "packages/sdfb-beam/src/sdfb_beam/pipeline.py";

export const CITES = {
  fkIntegrityLine: { source: `${PIPELINE}:362`, excerpt: "# Line 4 of defense (ADR 0031)" },
  panderaBatch: { source: `${PIPELINE}:382`, excerpt: "beam.BatchElements(" },
  uniquenessLine: { source: `${PIPELINE}:390`, excerpt: "# Line 3 of defense" },
  gateAfterLoad: { source: `${PIPELINE}:459`, excerpt: "# Order the gate AFTER the FILE_LOADS load jobs commit" },
  loadSafety: {
    source: "packages/sdfb-beam/src/sdfb_beam/dofns/validate_record.py:50",
    excerpt: "# Load-safety bound",
  },
  article1LineThree: {
    source: "docs/articles/01-building-banking-synthetic-data-intro.md:154",
    excerpt: "BigQuery itself refuses at load time",
  },
  skillLineThree: { source: ".claude/skills/validation-mode-a.md:28", excerpt: "## Line 3 — BigQueryIO `FailedRows`" },
  readmeUniqueness: { source: "README.md:204", excerpt: "3. **Uniqueness**" },
  gateRatio: {
    source: "packages/sdfb-core/src/sdfb_core/validation/summary.py:182",
    excerpt: "observed = (blocker_count / total)",
  },
  adr0033D2: {
    source: "docs/adr/0033-pool-ladder-integrity-at-scale.md:58",
    excerpt: "**D2 — The source filter's cardinality sizes the pool target",
  },
  poolTarget: {
    source: "packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py:1569",
    excerpt: "def _pool_target(",
  },
  article2Honest: {
    source: "docs/articles/02-type-system-freetext-resolution.md:262",
    excerpt: "**Pros and cons, honestly.**",
  },
  ws5PoolLlm: {
    source: "docs/designs/2026-07-26-ws5-generation-throughput.md:40",
    excerpt: "`freetext_pool_built` (LLM service time)",
  },
} as const satisfies Record<string, Citation>;

/** "path:123" → { path, line }. */
export function splitCitation(source: string): { path: string; line: number } {
  const match = /^(.*):(\d+)$/.exec(source);
  if (!match?.[1] || !match[2]) throw new Error(`not a path:line citation: ${source}`);
  return { path: match[1], line: Number(match[2]) };
}
