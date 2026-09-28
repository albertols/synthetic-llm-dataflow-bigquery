/**
 * The nine stages of the hero pipeline, from the source table to the
 * evaluation registry. Each stage's quote is copied from the file named in
 * `source` (checked by content.test.ts); its chips are computed from the
 * contracts or the live facets, never typed.
 */
import {
  ArrowDownToLine,
  ChartColumn,
  CircuitBoard,
  Database,
  Gauge,
  ListChecks,
  Rows3,
  ShieldCheck,
  Waypoints,
  type LucideIcon,
} from "lucide-react";

import type { Facets } from "@contracts/api";

import { formatCount, formatFixed } from "@/lib/format";

import { facts, referenceDkw } from "./contractValues";
import type { IntroTarget } from "./targets";

export type StageKind = "store" | "launcher" | "cpu" | "gpu" | "beam" | "evaluation";

export const KIND_META: Record<StageKind, { label: string; short: string; color: string; text: string }> = {
  store: { label: "BigQuery table", short: "BigQuery", color: "var(--brand-blue)", text: "text-link" },
  launcher: { label: "Launcher · CPU", short: "Launcher", color: "var(--cpu)", text: "text-cpu-text" },
  cpu: { label: "Worker · CPU", short: "CPU", color: "var(--cpu)", text: "text-cpu-text" },
  gpu: { label: "Worker · GPU", short: "GPU", color: "var(--gpu)", text: "text-gpu-text" },
  beam: { label: "Beam transform", short: "Beam", color: "var(--accent)", text: "text-accent-text" },
  evaluation: { label: "Beam · evaluation job", short: "Beam", color: "var(--accent)", text: "text-accent-text" },
};

export type PhaseId = "read" | "generate" | "guard" | "evaluate";

export const PHASES: ReadonlyArray<{ id: PhaseId; label: string; where: string; span: number }> = [
  { id: "read", label: "Read the source", where: "launcher", span: 3 },
  { id: "generate", label: "Generate", where: "GPU workers", span: 2 },
  { id: "guard", label: "Guard and land", where: "same job", span: 2 },
  { id: "evaluate", label: "Evaluate", where: "separate job", span: 2 },
];

/** The two Dataflow jobs the stages belong to (spans in stages). */
export const JOBS: ReadonlyArray<{ label: string; detail: string; span: number; isNew?: boolean }> = [
  { label: "Generation job", detail: "one Dataflow job, parents first", span: 7 },
  { label: "Evaluation job", detail: "sdfb-evaluation", span: 2, isNew: true },
];

export type StageQuote = {
  text: string;
  /** Repo-relative path of the quoted file. */
  source: string;
  anchor?: string;
};

export type StageContext = { facets?: Facets | undefined };

export type Stage = {
  id: string;
  title: string;
  kind: StageKind;
  icon: LucideIcon;
  phase: PhaseId;
  /** One line in this app's words. */
  summary: string;
  /** Copied verbatim from `source` (links stripped). */
  quote: StageQuote;
  concept: string;
  /** Knob and catalogue numbers, and live counts once the facets arrive. */
  chips: (context: StageContext) => string[];
  target: (context: StageContext) => IntroTarget;
  isNew?: boolean;
};

const count = (value: number | null | undefined, unit: string) =>
  value === null || value === undefined ? null : `${formatCount(value)} ${unit}`;

const present = (values: Array<string | null>) => values.filter((value): value is string => value !== null);

export const STAGES: readonly Stage[] = [
  {
    id: "source",
    title: "Source table",
    kind: "store",
    icon: Database,
    phase: "read",
    summary: "The real table: its DDL and a bounded sample are all the job reads.",
    quote: {
      text: "the real BigQuery table being imitated; only its DDL and a bounded sample (≤10k rows) are ever read.",
      source: "README.md",
      anchor: "glossary",
    },
    concept: "intro:source-table",
    chips: ({ facets }) =>
      present(["DDL + sample", facets ? count(facets.source_tables.length, "profiled tables") : null]),
    target: () => ({ tab: "config", section: "sources" }),
  },
  {
    id: "reference",
    title: "Reference sample",
    kind: "launcher",
    icon: Rows3,
    phase: "read",
    summary: "A live SELECT in a fixed order; every distribution is measured from it.",
    quote: {
      text: "Reference rows are a live, bounded `SELECT` with a deterministic order (ADR 0005), so a run is reproducible from its inputs.",
      source: "docs/DESIGN.md",
      anchor: "1-architecture-and-the-cpugpu-split",
    },
    concept: "intro:reference-sample",
    chips: () => {
      const eps = referenceDkw();
      return present([count(facts.referenceRows, "rows"), eps === null ? null : `DKW ε ≈ ${formatFixed(eps, 4)}`]);
    },
    target: () => ({ tab: "config", section: "sources" }),
  },
  {
    id: "stats",
    title: "source_table_stats",
    kind: "store",
    icon: ChartColumn,
    phase: "read",
    summary: "A per-column profile measured once, driver-side, and persisted.",
    quote: {
      text: "the persisted per-column profile (entropy, deciles, null/empty fractions, temporal mixes) measured once driver-side; workers never query it.",
      source: "README.md",
      anchor: "statistical-concepts",
    },
    concept: "intro:source-table-stats",
    chips: () =>
      present([
        facts.statsTiers.length ? `tiers: ${facts.statsTiers.join(" · ")}` : null,
        facts.profilerVersion === null ? null : `profiler v${facts.profilerVersion}`,
      ]),
    target: () => ({ tab: "config", section: "sources" }),
  },
  {
    id: "rag",
    title: "RAG retrieval",
    kind: "cpu",
    icon: Waypoints,
    phase: "generate",
    summary: "Rows become text chunks and unit vectors; an exact index retrieves exemplars.",
    quote: {
      text: "FAISS `IndexFlatIP` (exact inner product over L2-normalized vectors = cosine; deterministic, single-threaded search) holds ≤1,024 vectors per worker.",
      source: "README.md",
      anchor: "generation-engines",
    },
    concept: "intro:rag",
    chips: () =>
      present([
        count(facts.rowDocs, "chunks"),
        facts.embedDim === null ? null : `${facts.embedDim}-d vectors`,
        facts.topK === null ? "IndexFlatIP" : `IndexFlatIP · top-${facts.topK}`,
      ]),
    target: () => ({ tab: "rag" }),
  },
  {
    id: "generate",
    title: "Generation on L4",
    kind: "gpu",
    icon: CircuitBoard,
    phase: "generate",
    summary: "vLLM builds bounded value pools once; NumPy samples every row.",
    quote: {
      text: "the LLM is asked what values a column can take, once, and rows are then sampled without it.",
      source: "docs/DESIGN.md",
      anchor: "2-engines-and-the-llm-as-a-distribution-estimator",
    },
    concept: "intro:generation-l4",
    chips: () => present(["vLLM · O(1) calls", facts.poolMax === null ? null : `pool ≤ ${formatCount(facts.poolMax)}`]),
    target: () => ({ tab: "config", section: "amp", channel: "generation" }),
  },
  {
    id: "mode-a",
    title: "Mode A guardrails",
    kind: "beam",
    icon: ShieldCheck,
    phase: "guard",
    summary: "Four gates; every reject goes to the DLQ with its reason.",
    quote: {
      text: "no row is dropped silently; every rejection is queryable with its reason, and the audit row lands even when the run fails.",
      source: "docs/DESIGN.md",
      anchor: "7-validation-and-the-dead-letter-queue",
    },
    concept: "intro:mode-a",
    chips: () => ["schema · uniqueness · PK · FK"],
    target: () => ({ tab: "config", knob: "uniqueness_mode" }),
  },
  {
    id: "land",
    title: "WriteLanding",
    kind: "beam",
    icon: ArrowDownToLine,
    phase: "guard",
    summary: "Batch load jobs into the landing table, the DLQ and validation_runs.",
    quote: {
      text: "All sinks use BigQuery `FILE_LOADS`: the job is batch-shaped.",
      source: "docs/DESIGN.md",
      anchor: "1-architecture-and-the-cpugpu-split",
    },
    concept: "intro:write-landing",
    chips: () => ["FILE_LOADS", "landing · dlq · validation_runs"],
    target: () => ({ tab: "config", knob: "write_disposition" }),
  },
  {
    id: "evaluate",
    title: "Evaluation",
    kind: "evaluation",
    icon: Gauge,
    phase: "evaluate",
    summary: "A separate job measures what landed against the live source.",
    quote: {
      text: "Standalone statistical evaluation of synthetic BigQuery tables against their live source: fidelity, privacy, and utility, computed with Apache Beam and written to BigQuery (`synthetic_data_quality.*`).",
      source: "packages/sdfb-evaluation/README.md",
    },
    concept: "intro:evaluation-job",
    chips: () => present([count(facts.catalogueMetrics, "metrics"), count(facts.catalogueLevels, "levels")]),
    target: () => ({ tab: "evaluation" }),
    isNew: true,
  },
  {
    id: "registry",
    title: "Registry",
    kind: "store",
    icon: ListChecks,
    phase: "evaluate",
    summary: "Append-only events per evaluation: RUNNING, then FINAL.",
    quote: {
      text: "The last event written per evaluation_id, RUNNING or FINAL.",
      source: "packages/sdfb-evaluation/src/sdfb_evaluation/schemas/views.sql",
    },
    concept: "intro:registry",
    chips: ({ facets }) =>
      present(["RUNNING → FINAL", facets ? count(facets.counts.evaluations, "evaluations") : null]),
    target: ({ facets }) =>
      facets?.latest ? { tab: "evaluation-run", evaluationId: facets.latest.evaluation_id } : { tab: "evaluation" },
  },
];
