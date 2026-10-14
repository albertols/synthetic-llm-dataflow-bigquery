/**
 * The package map: what each workspace package is (descriptions copied from
 * the README's workspace layout, the package manifests and ADR 0042), what it
 * may import, and the ADRs that fix those rules.
 */
export type PackageId = "sdfb-core" | "sdfb-beam" | "sdfb-evaluation" | "gui";

export type PackageCard = {
  id: PackageId;
  path: string;
  /** Copied from the file named in `descriptionSource`. */
  description: string;
  descriptionSource: string;
  imports: string;
  runsOn: string;
  /** ADR numbers; one missing from DESIGN.md's map is shown as pending. */
  adrs: readonly string[];
};

export const PACKAGES: readonly PackageCard[] = [
  {
    id: "sdfb-core",
    path: "packages/sdfb-core",
    description: "pure-Python contracts, codegen, engines, prompt templates (no Beam/GCP/torch)",
    descriptionSource: "README.md",
    imports: "Nothing from this repository: no Beam, no GCP, no torch.",
    runsOn: "Laptop tests, the launcher and every worker",
    adrs: ["0006", "0013", "0022", "0032"],
  },
  {
    id: "sdfb-beam",
    path: "packages/sdfb-beam",
    description: "Apache Beam pipeline + DoFns; extras: [gpu] vllm+torch, [embedding] faiss, [library] sdgx",
    descriptionSource: "README.md",
    imports: "sdfb-core, never the other way.",
    runsOn: "Dataflow: the Flex Template launcher and the GPU workers",
    adrs: ["0009", "0014", "0030", "0034"],
  },
  {
    id: "sdfb-evaluation",
    path: "packages/sdfb-evaluation",
    description:
      "Standalone statistical evaluation of synthetic BigQuery tables against their source (Apache Beam, BigQuery).",
    descriptionSource: "packages/sdfb-evaluation/pyproject.toml",
    imports: "None of the generator packages; an AST test enforces it.",
    runsOn: "Its own Dataflow job, after the landing tables exist",
    adrs: ["0041"],
  },
  {
    id: "gui",
    path: "gui",
    description:
      "Synthetic Platform: a self-hosted GUI that reads and explains this project's evaluation, run, source-stats and RAG data (ADR 0042).",
    descriptionSource: "gui/package.json",
    imports: "No Python. Types are generated from the Python schemas, catalogue and knobs.",
    runsOn: "Your machine (127.0.0.1), or one Cloud Run container",
    adrs: ["0042", "0040", "0001"],
  },
];

/** Import direction (solid) and data or contract flow (dashed). Colours are the DESIGN.md classes. */
export const PACKAGE_CHART = `flowchart LR
  classDef core  fill:#1baf7a,color:#fff,stroke:#127a55
  classDef beam  fill:#eb6834,color:#fff,stroke:#b44f26
  classDef eval  fill:#7a3fd1,color:#fff,stroke:#5a2f9d
  classDef gui   fill:#2a78d6,color:#fff,stroke:#1d5599
  classDef store fill:#6b7280,color:#fff,stroke:#4b5563

  CORE["sdfb-core<br/>engines · contracts · stats"]:::core
  BEAM["sdfb-beam<br/>pipeline · DoFns · vLLM client"]:::beam
  TESTS["sdfb-tests<br/>unit · DirectRunner"]:::store
  EVAL["sdfb-evaluation<br/>standalone job"]:::eval
  GUI["gui<br/>SPA + local BFF"]:::gui
  BQ[("BigQuery<br/>landing · quality · evaluation")]:::store

  BEAM -->|imports| CORE
  TESTS -->|imports| CORE
  TESTS -->|imports| BEAM
  BEAM -.->|writes| BQ
  EVAL -.->|reads, writes evaluation_*| BQ
  GUI -.->|named read-only SELECTs| BQ
  GUI -.->|generates types from| EVAL
  GUI -.->|exports knobs from| BEAM`;

export const PACKAGE_CHART_LABEL =
  "sdfb-beam imports sdfb-core; sdfb-tests imports both. sdfb-evaluation imports neither and reads and writes BigQuery on its own. The GUI imports no Python: it generates its types from the evaluation schemas and exports knob defaults from the pipeline code, and reads BigQuery through named read-only queries.";
