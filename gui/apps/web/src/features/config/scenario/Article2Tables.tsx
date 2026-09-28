/**
 * Article 2's two tables on the 10k reference sample ("Pros and cons,
 * honestly" and "What you can set, and what happens when you do"), reused
 * verbatim — docs/articles/02-type-system-freetext-resolution.md, the
 * "reference sample" section. Backticked names render as code.
 */
import { CircleCheck, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import { DOCS, docUrl } from "../model/links";

/** `code` spans → <code>. */
function md(text: string): ReactNode {
  return text.split(/(`[^`]+`)/g).map((part, i) =>
    part.startsWith("`") && part.endsWith("`") ? (
      <code key={i} className="font-mono text-xs text-text-1">
        {part.slice(1, -1)}
      </code>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

const PROS_CONS: Array<{ pro: boolean; property: string; why: string }> = [
  {
    pro: true,
    property: "cheap and fast",
    why: "one query, cents, seconds; profiling is driver-side and costs the DAG nothing",
  },
  {
    pro: true,
    property: "deterministic",
    why: "same contents → same digest → warm pools, warm chunks, comparable validation scores",
  },
  {
    pro: true,
    property: "bounded blast radius",
    why: "reference rows live in the driver and the workers' setup; nothing per-row ever touches the table",
  },
  {
    pro: true,
    property: "enough for what it is asked",
    why: "marginals, types, shapes, and eight seeds plateau long before 10k",
  },
  { pro: false, property: "blind to rarity and tails", why: "anything below ~0.1% share or beyond p99.9 is a guess" },
  {
    pro: false,
    property: "blind to cardinality",
    why: "never trust a sample distinct count — the code no longer does",
  },
  {
    pro: false,
    property: "live, so it drifts",
    why: "two runs on a changing table see different rows; compare digests before comparing scores",
  },
  {
    pro: false,
    property: "PII is not masked in the sample",
    why: "a DEV-only assumption today; the sample is real data in the driver's memory and, redacted, in the logs",
  },
];

const KNOB_AFTERMATH: Array<{ knob: string; def: string; effect: string; aftermath: string }> = [
  {
    knob: "`--reference_rows_limit`",
    def: "10,000",
    effect: "more rows → smaller ε (4× rows per halving), better tails and rare categories",
    aftermath:
      "a new digest: every pool and chunk rebuilds, the run is cold; driver memory and profiling time grow linearly; cardinality is still truncated at n",
  },
  {
    knob: "`--source_stats`",
    def: "`sample`",
    effect:
      "`exact` adds one aggregate scan: HLL++ distinct, deciles, top-k over the whole table; exact distinct sizes the pool target",
    aftermath:
      "one BigQuery scan of the source per run; a failed scan degrades loudly to the sample tier (`source_stats_exact_failed`), never kills the run",
  },
  {
    knob: "`--source_stats_table` / `--source_stats_json`",
    def: "off",
    effect: "persist the stats rows / artifact",
    aftermath: "rows keyed by `(table, digest, tier, profiler version)`; existing rows are never rewritten",
  },
  {
    knob: "`--reference_table`",
    def: "required",
    effect: "which table the sample is drawn from",
    aftermath:
      'the digest is the only provenance of "what rows did we see"; a filtered or different reference changes the population silently — the digest changes, the log does not explain why',
  },
  {
    knob: "stratified sampling",
    def: "not a flag",
    effect: "a per-stratum `QUALIFY ROW_NUMBER() OVER (PARTITION BY … ORDER BY FARM_FINGERPRINT(…))` query",
    aftermath: "the design doc's remedy for rare segments; not wired into the launcher today",
  },
];

export function Article2Tables() {
  return (
    <div className="grid gap-5">
      <div className="grid gap-2">
        <h3 className="text-base font-semibold text-text-1">Pros and cons, honestly</h3>
        <TableContainer aria-label="Pros and cons of the 10k reference sample">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead scope="col">Property</TableHead>
                <TableHead scope="col">Why it matters</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {PROS_CONS.map((row) => (
                <TableRow key={row.property}>
                  <TableCell className="whitespace-nowrap">
                    <span className="inline-flex items-center gap-1.5 font-medium text-text-1">
                      {row.pro ? (
                        <CircleCheck className="size-4 text-status-good-text" aria-hidden="true" />
                      ) : (
                        <TriangleAlert className="size-4 text-status-warn-text" aria-hidden="true" />
                      )}
                      <span className="sr-only">{row.pro ? "Pro: " : "Con: "}</span>
                      {row.property}
                    </span>
                  </TableCell>
                  <TableCell className="min-w-64 text-text-2">{md(row.why)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </div>
      <div className="grid gap-2">
        <h3 className="text-base font-semibold text-text-1">What you can set, and what happens when you do</h3>
        <TableContainer aria-label="Reference-sample knobs and their aftermath">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead scope="col">Knob</TableHead>
                <TableHead scope="col">Default</TableHead>
                <TableHead scope="col">Effect</TableHead>
                <TableHead scope="col">The aftermath</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {KNOB_AFTERMATH.map((row) => (
                <TableRow key={row.knob}>
                  <TableCell className="min-w-40">{md(row.knob)}</TableCell>
                  <TableCell className="whitespace-nowrap">{md(row.def)}</TableCell>
                  <TableCell className="min-w-56 text-text-2">{md(row.effect)}</TableCell>
                  <TableCell className="min-w-64 text-text-2">{md(row.aftermath)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </div>
      <p className="text-sm text-text-2">
        The rule of thumb that falls out of this: do not raise the sample to fix cardinality or privacy — those are
        answered against the table. Raise it when a tail or a rare category matters, and expect a cold run.{" "}
        <a href={docUrl(DOCS.article2, 262)} target="_blank" rel="noopener noreferrer" className="text-link underline">
          Article 2, the reference sample
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      </p>
    </div>
  );
}
