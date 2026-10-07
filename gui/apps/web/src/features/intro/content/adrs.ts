/**
 * The ADR reference map of docs/DESIGN.md §9, copied verbatim (number, the
 * section that summarizes it, the record's title and file). `content.test.ts`
 * re-reads the map between its `adr-map` markers and fails on any drift.
 */
import { repoBlobUrl } from "@/lib/links";

export type AdrRef = {
  /** Four digits, "0036". */
  number: string;
  /** The DESIGN.md section that summarizes it. */
  section: number;
  /** The title as the map prints it (backticks mark code). */
  title: string;
  /** File name under docs/adr/. */
  file: string;
};

export const ADRS: readonly AdrRef[] = [
  {
    number: "0001",
    section: 1,
    title: "No managed GCP services in the serving path",
    file: "0001-no-managed-gcp-services.md",
  },
  { number: "0002", section: 3, title: "Open-weight model shortlist", file: "0002-gemma-4-model-shortlist.md" },
  { number: "0003", section: 1, title: "Private container registry (withdrawn)", file: "0003-jfrog-image-registry.md" },
  { number: "0004", section: 1, title: "Dataflow region", file: "0004-europe-west3-region.md" },
  { number: "0005", section: 1, title: "Live SELECT for reference rows", file: "0005-live-select-reference-data.md" },
  {
    number: "0006",
    section: 2,
    title: "`GenerationEngine` ABC and `ModelClient` Protocol",
    file: "0006-generation-engine-abc.md",
  },
  {
    number: "0007",
    section: 9,
    title: "One source of truth across docs and code",
    file: "0007-dry-documentation-policy.md",
  },
  { number: "0008", section: 1, title: "Images are built in CI", file: "0008-ci-driven-builds.md" },
  {
    number: "0009",
    section: 1,
    title: "One image for launcher and workers",
    file: "0009-single-flex-template-image.md",
  },
  { number: "0010", section: 2, title: "Local smoke test on Apple Silicon", file: "0010-m4-local-smoke-mlx.md" },
  {
    number: "0011",
    section: 3,
    title: "Adopt Beam's vLLM handler (amended by 0014)",
    file: "0011-adopt-beam-vllm-model-handler.md",
  },
  {
    number: "0012",
    section: 1,
    title: "Image build under network constraints (withdrawn)",
    file: "0012-enterprise-image-build.md",
  },
  {
    number: "0013",
    section: 2,
    title: "The LLM as a distribution estimator",
    file: "0013-distribution-estimator-spine.md",
  },
  {
    number: "0014",
    section: 3,
    title: "The model client owns the vLLM server",
    file: "0014-vllm-model-client-owns-server.md",
  },
  {
    number: "0015",
    section: 1,
    title: "Worker image in Artifact Registry",
    file: "0015-worker-image-via-artifact-registry.md",
  },
  { number: "0016", section: 1, title: "Images on Cloud Build", file: "0016-personal-gcp-cloud-build.md" },
  {
    number: "0017",
    section: 2,
    title: "Own retrieval layer instead of `apache_beam.ml.rag`",
    file: "0017-custom-rag-layer-over-beam-ml-rag.md",
  },
  {
    number: "0018",
    section: 5,
    title: "Batched, parallel free-text pool builds",
    file: "0018-parallel-batched-freetext-pools.md",
  },
  {
    number: "0019",
    section: 2,
    title: "Embedding population scoped to its consumers",
    file: "0019-rag-population-scoped-to-consumers.md",
  },
  {
    number: "0020",
    section: 5,
    title: "Free-text pools as a persisted artifact",
    file: "0020-freetext-pools-as-persisted-artifact.md",
  },
  {
    number: "0021",
    section: 4,
    title: "Relational contract in column descriptions (superseded by 0032)",
    file: "0021-relational-contract-in-descriptions.md",
  },
  {
    number: "0022",
    section: 5,
    title: "Source table statistics as a generation input",
    file: "0022-stats-driven-generation.md",
  },
  {
    number: "0023",
    section: 5,
    title: "Pools reject against the full source domain",
    file: "0023-source-domain-pool-rejection.md",
  },
  {
    number: "0024",
    section: 5,
    title: "Structured prompt-constraint templates",
    file: "0024-structured-prompt-constraint-templates.md",
  },
  {
    number: "0025",
    section: 5,
    title: "Marginal fidelity by construction",
    file: "0025-marginal-fidelity-by-construction.md",
  },
  {
    number: "0026",
    section: 5,
    title: "Measurement first, then mask integrity",
    file: "0026-measurement-first-mask-integrity.md",
  },
  {
    number: "0027",
    section: 5,
    title: "Verification cycle and operational integrity",
    file: "0027-verified-wave4-operational-integrity.md",
  },
  {
    number: "0028",
    section: 5,
    title: "Constraint router and relational plan",
    file: "0028-constraint-router-relational-plan.md",
  },
  {
    number: "0029",
    section: 4,
    title: "Launch scenarios for a relationship model",
    file: "0029-fk-model-scenarios-and-history-mappings.md",
  },
  {
    number: "0030",
    section: 4,
    title: "Single-job relational generation",
    file: "0030-single-job-relational-generation.md",
  },
  { number: "0031", section: 4, title: "Joint foreign-key draws", file: "0031-joint-fk-key-draws.md" },
  { number: "0032", section: 8, title: "Relationships are configuration", file: "0032-relationships-as-config.md" },
  {
    number: "0033",
    section: 5,
    title: "Pool-ladder integrity at scale",
    file: "0033-pool-ladder-integrity-at-scale.md",
  },
  {
    number: "0034",
    section: 6,
    title: "One barrier, shared engines",
    file: "0034-generation-throughput-single-barrier-shared-engines.md",
  },
  {
    number: "0035",
    section: 4,
    title: "Key capacity counts foreign-key-bound members",
    file: "0035-pk-capacity-fk-bound-members.md",
  },
  {
    number: "0036",
    section: 4,
    title: "Parent-driven fan-out generation",
    file: "0036-parent-driven-fanout-generation.md",
  },
  { number: "0037", section: 4, title: "Multi-parent children", file: "0037-multi-parent-children.md" },
  {
    number: "0038",
    section: 4,
    title: "Measured conflicts adjust the model",
    file: "0038-measured-conflicts-adjust-the-model.md",
  },
  {
    number: "0039",
    section: 4,
    title: "Row projection before the graph (proposed)",
    file: "0039-row-projection-before-the-graph.md",
  },
  {
    number: "0040",
    section: 9,
    title: "Publishing this design to the Dataflow Solution Guides",
    file: "0040-dsg-donation-golden-source-sync.md",
  },
  {
    number: "0041",
    section: 11,
    title: "Evaluation is a standalone package and a separate job",
    file: "0041-evaluation-standalone-package.md",
  },
  {
    number: "0042",
    section: 12,
    title: "A self-hosted GUI; managed dashboards stay out",
    file: "0042-self-hosted-platform-gui.md",
  },
];

const BY_NUMBER = new Map(ADRS.map((adr) => [adr.number, adr]));

/** The ADR with this number, if DESIGN.md's map lists it. */
export function findAdr(number: string): AdrRef | undefined {
  return BY_NUMBER.get(number);
}

/** The ADRs a DESIGN.md section summarizes, in number order. */
export function adrsForSection(section: number): AdrRef[] {
  return ADRS.filter((adr) => adr.section === section);
}

export function adrUrl(adr: AdrRef): string {
  return repoBlobUrl(`docs/adr/${adr.file}`);
}

/** The title without markdown code ticks, for aria-labels and tooltips. */
export function plainTitle(title: string): string {
  return title.replaceAll("`", "");
}
