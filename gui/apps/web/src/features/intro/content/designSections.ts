/**
 * "How it works": one card per docs/DESIGN.md section, §1–§12.
 *
 * Every quote is copied, not written: a section's bold **Claim** sentence, or,
 * where a section has none (§8, §9, §10), its lead sentence labelled "Lead".
 * Captions are the italic figure captions of DESIGN.md. §11 does not exist on
 * this branch yet — it lands with the evaluation package (ADR 0041) — so its
 * card is marked pending and quotes the package README instead.
 * `content.test.ts` checks each quote and caption against its source file.
 */
import { repoBlobUrl } from "@/lib/links";

import type { IntroTarget } from "./targets";

export type QuoteSource = "DESIGN.md" | "packages/sdfb-evaluation/README.md";

export type Quote = {
  /** "claim": the section's bold Claim sentence; "lead": its first sentence when it has no claim. */
  kind: "claim" | "lead";
  /** Verbatim (links stripped; backticks mark code). */
  text: string;
  source: QuoteSource;
};

export type ImageVisual = {
  kind: "image";
  /** File name under /assets (synced by `npm run assets:sync`, listed in provenance.json). */
  file: string;
  /** The figure's own alt text in its document, or its printed title. */
  alt: string;
  /** DESIGN.md's italic caption under the figure, verbatim. */
  caption?: string;
};

export type SectionVisual =
  | ImageVisual
  | { kind: "mermaid"; chart: string; ariaLabel: string; caption?: string }
  | { kind: "adr-map" }
  | { kind: "figure-mosaic" };

export type DesignSection = {
  number: number;
  /** The heading text after "## N. ", verbatim. */
  title: string;
  /** GitHub's anchor for the heading. */
  anchor: string;
  /** "pending": the section is not in DESIGN.md on this branch yet. */
  status: "published" | "pending";
  quote: Quote;
  /** Further claims of the same section (shown in the detail view). */
  more?: Quote[];
  visual: SectionVisual;
  /** Where to explore it in this app. */
  explore?: { label: string; target: IntroTarget };
};

export const DESIGN_PATH = "docs/DESIGN.md";

export function designUrl(anchor?: string): string {
  return `${repoBlobUrl(DESIGN_PATH)}${anchor ? `#${anchor}` : ""}`;
}

/** DESIGN.md §12's first diagram, copied verbatim. */
const GUI_CHART = `flowchart LR
  classDef beam  fill:#eb6834,color:#fff,stroke:#b44f26
  classDef cpu   fill:#1baf7a,color:#fff,stroke:#127a55
  classDef store fill:#2a78d6,color:#fff,stroke:#1d5599

  JOB["🔀 Dataflow job<br/>generate + validate"]:::beam
  BQ[("🗄️ project tables<br/>quality, evaluation,<br/>source stats")]:::store
  WEB["⚙️ browser app<br/>holds no credentials"]:::cpu
  BFF["⚙️ local BFF<br/>named queries only"]:::cpu
  CAP["🛡️ dry run<br/>+ bytes cap"]:::cpu
  MOCK[("📄 seeded mock<br/>fixtures")]:::store

  JOB -->|"FILE_LOADS"| BQ
  WEB -->|"query name<br/>+ parameters"| BFF
  BFF -->|"DATA_SOURCE=bigquery"| CAP
  CAP -->|"read-only SELECT"| BQ
  BFF -->|"DATA_SOURCE=mock"| MOCK`;

export const DESIGN_SECTIONS: readonly DesignSection[] = [
  {
    number: 1,
    title: "Architecture and the CPU/GPU split",
    anchor: "1-architecture-and-the-cpugpu-split",
    status: "published",
    quote: {
      kind: "claim",
      text: "one Dataflow job reads a bounded sample, touches the GPU a bounded number of times, and writes validated rows; nothing leaves the project.",
      source: "DESIGN.md",
    },
    visual: { kind: "image", file: "architecture-overview.png", alt: "Architecture overview" },
  },
  {
    number: 2,
    title: "Engines and the LLM as a distribution estimator",
    anchor: "2-engines-and-the-llm-as-a-distribution-estimator",
    status: "published",
    quote: {
      kind: "claim",
      text: "the LLM is asked what values a column can take, once, and rows are then sampled without it.",
      source: "DESIGN.md",
    },
    visual: {
      kind: "image",
      file: "generation-plan-routing.png",
      alt: "generation_plan — how every column gets its synthesis strategy",
    },
    explore: { label: "Explore the RAG layer", target: { tab: "rag" } },
  },
  {
    number: 3,
    title: "Serving: a vLLM server owned by the engine",
    anchor: "3-serving-a-vllm-server-owned-by-the-engine",
    status: "published",
    quote: {
      kind: "claim",
      text: "each worker runs one vLLM OpenAI-compatible server, started by the first model call and sized to the GPU it finds",
      source: "DESIGN.md",
    },
    more: [
      {
        kind: "claim",
        text: "Beam's handlers run inference over a `PCollection` of prompts; here the prompts are decided one at a time by a loop that reads the previous answer, and a Beam graph has no loop.",
        source: "DESIGN.md",
      },
    ],
    visual: {
      kind: "image",
      file: "runtime-fleet-topology.png",
      alt: "One worker pool, every stage — what Dataflow starts for a run, and what each GPU worker pulls",
    },
    explore: {
      label: "Serving knobs",
      target: { tab: "config", knob: "client_type" },
    },
  },
  {
    number: 4,
    title: "Relational generation",
    anchor: "4-relational-generation",
    status: "published",
    quote: {
      kind: "claim",
      text: "a child table is generated from its parent's landed keys, so the parent/child ratio, the child's key uniqueness and referential integrity hold by construction rather than by rejection.",
      source: "DESIGN.md",
    },
    visual: {
      kind: "image",
      file: "fanout-generation.png",
      alt: "Parent-driven fan-out",
      caption:
        "Left: a child's size is the parent key count times the source's children-per-parent histogram, zero bucket included, so it is derived and not requested. Right: inside one parent key, drawing the key-completing cells at random collides; drawing them without replacement never does.",
    },
    explore: {
      label: "Relational knobs",
      target: { tab: "config", knob: "generate_fk_relationships" },
    },
  },
  {
    number: 5,
    title: "Fidelity, value pools and constraints",
    anchor: "5-fidelity-value-pools-and-constraints",
    status: "published",
    quote: {
      kind: "claim",
      text: "numeric and temporal values are drawn where the source is dense, not uniformly across its range.",
      source: "DESIGN.md",
    },
    visual: {
      kind: "image",
      file: "stats-inverse-cdf.png",
      alt: "Inverse transform sampling through the 11-point decile vector",
      caption: "Uniform-in-range sampling flattens a skewed column; sampling through its deciles preserves the shape.",
    },
    explore: {
      label: "Source-stats knobs",
      target: { tab: "config", knob: "source_stats" },
    },
  },
  {
    number: 6,
    title: "Throughput",
    anchor: "6-throughput",
    status: "published",
    quote: {
      kind: "claim",
      text: "with the model called a bounded number of times, the job's time is CPU generation and shuffle, not the GPU.",
      source: "DESIGN.md",
    },
    visual: {
      kind: "image",
      file: "throughput-where-time-went.png",
      alt: "Where time went",
      caption:
        "Warming the pools removes only the pool branch; CPU generation and the uniqueness shuffle dominate both the cold and the warm job.",
    },
    explore: {
      label: "Fleet knobs",
      target: { tab: "config", knob: "autoscaling" },
    },
  },
  {
    number: 7,
    title: "Validation and the dead-letter queue",
    anchor: "7-validation-and-the-dead-letter-queue",
    status: "published",
    quote: {
      kind: "claim",
      text: "no row is dropped silently; every rejection is queryable with its reason, and the audit row lands even when the run fails.",
      source: "DESIGN.md",
    },
    visual: {
      kind: "image",
      file: "validation-guardrails.png",
      alt: "Three lines of defense — the guardrail between the LLM and the lakehouse",
    },
    explore: {
      label: "Guardrail knobs",
      target: { tab: "config", knob: "env" },
    },
  },
  {
    number: 8,
    title: "Configuration",
    anchor: "8-configuration",
    status: "published",
    quote: {
      kind: "lead",
      text: "Relational structure is configuration, in `config/relationships/*.yaml` (ADR 0032).",
      source: "DESIGN.md",
    },
    visual: {
      kind: "image",
      file: "relationships-scenarios.png",
      alt: "What each scenario generates",
      caption:
        "One model, four launches: the landing table and one flag decide which tables a launch generates and which edges draw keys.",
    },
    explore: {
      label: "All knobs",
      target: { tab: "config", knob: "relationships_uri" },
    },
  },
  {
    number: 9,
    title: "ADR reference map",
    anchor: "9-adr-reference-map",
    status: "published",
    quote: { kind: "lead", text: "Decision records live in the source repository.", source: "DESIGN.md" },
    visual: { kind: "adr-map" },
  },
  {
    number: 10,
    title: "Figure provenance",
    anchor: "10-figure-provenance",
    status: "published",
    quote: {
      kind: "lead",
      text: "Every figure is generated by a committed script and is shared with the design document that owns it; none was drawn for this page, and this page types no measured number of its own.",
      source: "DESIGN.md",
    },
    visual: { kind: "figure-mosaic" },
  },
  {
    number: 11,
    title: "Evaluation (sdfb-evaluation)",
    anchor: "11-evaluation-sdfb-evaluation",
    status: "pending",
    quote: {
      kind: "lead",
      text: "Standalone statistical evaluation of synthetic BigQuery tables against their live source: fidelity, privacy, and utility, computed with Apache Beam and written to BigQuery (`synthetic_data_quality.*`).",
      source: "packages/sdfb-evaluation/README.md",
    },
    visual: {
      kind: "image",
      file: "eval-ks-vs-wasserstein.png",
      alt: "KS vs Wasserstein: same W1, 5x different KS",
    },
    explore: { label: "Open the evaluations", target: { tab: "evaluation" } },
  },
  {
    number: 12,
    title: "Platform GUI",
    anchor: "12-platform-gui",
    status: "published",
    quote: {
      kind: "claim",
      text: "a self-hosted app reads this project's BigQuery tables through named, read-only, bytes-capped queries; it writes nothing, and the pipeline does not depend on it.",
      source: "DESIGN.md",
    },
    visual: {
      kind: "mermaid",
      chart: GUI_CHART,
      ariaLabel:
        "The Dataflow job writes the project tables with FILE_LOADS. The browser app sends a query name and parameters to the local BFF, which dry-runs and caps the bytes before a read-only SELECT, or serves seeded mock fixtures.",
      caption:
        "Nothing points back from the GUI to the job: the pipeline writes its tables as before, and the GUI only reads them.",
    },
  },
];

/** Where a quote's source file lives in the repository. */
export function quoteSourceUrl(quote: Quote, section: DesignSection): string {
  return quote.source === "DESIGN.md" ? designUrl(section.anchor) : repoBlobUrl(quote.source);
}
