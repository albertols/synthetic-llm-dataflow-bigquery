/**
 * INTRO concepts (namespace `intro:`): the pipeline stages of the hero, the
 * counters, the package map, the relationship shapes and edge roles, and the
 * vocabulary of the "How it works" cards. Owned by the INTRO tab (task G1).
 *
 * Wording follows docs/DESIGN.md, the README and the relationship-model docs;
 * numbers that live in the contracts (knob defaults, catalogue sizes) are
 * rendered by the UI from those contracts, never typed here.
 */
import { defineConcepts } from "../concept";

const REPO = "https://github.com/albertols/synthetic-llm-dataflow-bigquery/blob/master";
const DESIGN = `${REPO}/docs/DESIGN.md`;
const adr = (file: string) => `${REPO}/docs/adr/${file}`;

const L = {
  design1: { label: "DESIGN.md §1 — architecture", url: `${DESIGN}#1-architecture-and-the-cpugpu-split`, kind: "docs" },
  design2: {
    label: "DESIGN.md §2 — engines",
    url: `${DESIGN}#2-engines-and-the-llm-as-a-distribution-estimator`,
    kind: "docs",
  },
  design4: { label: "DESIGN.md §4 — relational generation", url: `${DESIGN}#4-relational-generation`, kind: "docs" },
  design7: {
    label: "DESIGN.md §7 — validation and the DLQ",
    url: `${DESIGN}#7-validation-and-the-dead-letter-queue`,
    kind: "docs",
  },
  design9: { label: "DESIGN.md §9 — ADR reference map", url: `${DESIGN}#9-adr-reference-map`, kind: "docs" },
  design10: { label: "DESIGN.md §10 — figure provenance", url: `${DESIGN}#10-figure-provenance`, kind: "docs" },
  relReadme: {
    label: "config/relationships/README.md",
    url: `${REPO}/config/relationships/README.md`,
    kind: "docs",
  },
  adr0005: {
    label: "ADR 0005 — live SELECT for reference rows",
    url: adr("0005-live-select-reference-data.md"),
    kind: "adr",
  },
  adr0006: { label: "ADR 0006 — GenerationEngine ABC", url: adr("0006-generation-engine-abc.md"), kind: "adr" },
  adr0007: { label: "ADR 0007 — one source of truth", url: adr("0007-dry-documentation-policy.md"), kind: "adr" },
  adr0013: {
    label: "ADR 0013 — the LLM as a distribution estimator",
    url: adr("0013-distribution-estimator-spine.md"),
    kind: "adr",
  },
  adr0014: {
    label: "ADR 0014 — the model client owns the vLLM server",
    url: adr("0014-vllm-model-client-owns-server.md"),
    kind: "adr",
  },
  adr0017: {
    label: "ADR 0017 — own retrieval layer",
    url: adr("0017-custom-rag-layer-over-beam-ml-rag.md"),
    kind: "adr",
  },
  adr0019: {
    label: "ADR 0019 — embedding population scoped to consumers",
    url: adr("0019-rag-population-scoped-to-consumers.md"),
    kind: "adr",
  },
  adr0022: { label: "ADR 0022 — source table statistics", url: adr("0022-stats-driven-generation.md"), kind: "adr" },
  adr0030: {
    label: "ADR 0030 — single-job relational generation",
    url: adr("0030-single-job-relational-generation.md"),
    kind: "adr",
  },
  adr0031: { label: "ADR 0031 — joint foreign-key draws", url: adr("0031-joint-fk-key-draws.md"), kind: "adr" },
  adr0032: {
    label: "ADR 0032 — relationships are configuration",
    url: adr("0032-relationships-as-config.md"),
    kind: "adr",
  },
  adr0036: {
    label: "ADR 0036 — parent-driven fan-out",
    url: adr("0036-parent-driven-fanout-generation.md"),
    kind: "adr",
  },
  adr0037: { label: "ADR 0037 — multi-parent children", url: adr("0037-multi-parent-children.md"), kind: "adr" },
  adr0042: { label: "ADR 0042 — self-hosted platform GUI", url: adr("0042-self-hosted-platform-gui.md"), kind: "adr" },
  catalogue: {
    label: "Metric catalogue (metrics.yaml)",
    url: `${REPO}/packages/sdfb-evaluation/src/sdfb_evaluation/catalogue/metrics.yaml`,
    kind: "code",
  },
  evalDesign: {
    label: "Evaluation framework design",
    url: `${REPO}/docs/designs/2026-07-07-evaluation-framework-design.md`,
    kind: "docs",
  },
  registrySql: {
    label: "views.sql — evaluation_latest",
    url: `${REPO}/packages/sdfb-evaluation/src/sdfb_evaluation/schemas/views.sql`,
    kind: "code",
  },
  thresholds: { label: "config/thresholds.yml", url: `${REPO}/config/thresholds.yml`, kind: "code" },
  shapesTest: {
    label: "test_relationship_shapes.py — every shape pinned",
    url: `${REPO}/packages/sdfb-tests/tests/unit/contracts/test_relationship_shapes.py`,
    kind: "code",
  },
  starDiamond: {
    label: "example_star_diamond.yaml",
    url: `${REPO}/config/relationships/example_star_diamond.yaml`,
    kind: "code",
  },
  retail: { label: "example_retail.yaml", url: `${REPO}/config/relationships/example_retail.yaml`, kind: "code" },
  thelook: {
    label: "gcp_public_fk_example.yaml (thelook)",
    url: `${REPO}/config/relationships/gcp_public_fk_example.yaml`,
    kind: "code",
  },
} as const;

const roleDiagram = "intro:edge-roles";

export const concepts = defineConcepts([
  // ------------------------------------------------------------ pipeline stages
  {
    id: "intro:source-table",
    title: "Source table",
    purpose:
      "The real BigQuery table being imitated. Only its DDL and a bounded, deterministically ordered sample are read, and nothing leaves the project.",
    interpretation: {
      tip: "Which source tables join a launch comes from the relationship model, never from a table description (ADR 0032).",
    },
    links: [L.design1, L.adr0005, L.adr0032],
  },
  {
    id: "intro:reference-sample",
    title: "Reference sample and the DKW band",
    purpose:
      "The bounded sample every distribution is measured from: a live SELECT of reference_rows_limit rows in a fixed order. The Dvoretzky–Kiefer–Wolfowitz inequality bounds how far its empirical CDF can sit from the source's, whatever the table's size.",
    formula:
      "P\\Big(\\sup_x \\lvert \\hat F_n(x) - F(x) \\rvert > \\varepsilon\\Big) \\le 2e^{-2n\\varepsilon^2} \\;\\Rightarrow\\; \\varepsilon(n) = \\sqrt{\\frac{\\ln(2/\\alpha)}{2n}}",
    interpretation: {
      good: "Every fraction and every quantile of the sample sits within ε of the source's, at confidence 1 − α.",
      bad: "The bound says nothing about distinct counts: that is why the exact source-stats tier exists.",
      tip: "ε shrinks as 1/√n: four times the rows halves the band. The stage label shows ε at the current default.",
    },
    pitfalls:
      "A category rarer than ε can be missing from the sample entirely; its capture probability is 1 − (1 − p)ⁿ.",
    diagram: "core:noise-floor",
    links: [
      { label: "Dvoretzky, Kiefer & Wolfowitz 1956", url: "https://doi.org/10.1214/aoms/1177728174", kind: "paper" },
      { label: "Massart 1990 — the tight constant", url: "https://doi.org/10.1214/aop/1176990746", kind: "paper" },
      L.adr0005,
      {
        label: "Reference-sample scaling design",
        url: `${REPO}/docs/designs/2026-07-24-reference-sample-scaling.md`,
        kind: "docs",
      },
    ],
  },
  {
    id: "intro:source-table-stats",
    title: "source_table_stats",
    purpose:
      "The persisted per-column profile — entropy, deciles, null and empty fractions, temporal mixes, null patterns — measured once, driver-side. Workers never query it.",
    formula: "H_{\\mathrm{norm}} = \\frac{-\\sum_i p_i \\log_2 p_i}{\\log_2 k}",
    interpretation: {
      tip: "The sample tier profiles the reference sample at no extra query cost; the exact tier adds one approximate-aggregate SELECT (HLL++) over the live table for true cardinality.",
    },
    links: [
      L.adr0022,
      {
        label: "Source-table statistics design",
        url: `${REPO}/docs/designs/2026-08-05-source-table-stats.md`,
        kind: "docs",
      },
      { label: "Shannon 1948 — entropy", url: "https://doi.org/10.1002/j.1538-7305.1948.tb01338.x", kind: "paper" },
      { label: "Heule et al. 2013 — HyperLogLog++", url: "https://doi.org/10.1145/2452376.2452456", kind: "paper" },
    ],
  },
  {
    id: "intro:rag",
    title: "RAG layer: chunks, vectors, an exact index",
    purpose:
      "Reference rows are serialized GReaT-style into text chunks, embedded as unit vectors and held in a FAISS IndexFlatIP, an exact inner-product index. Retrieval picks the exemplars that condition the model: the most typical (centroid top-k) or the most diverse (greedy k-center).",
    formula:
      "\\cos\\theta = \\frac{u \\cdot v}{\\lVert u \\rVert\\,\\lVert v \\rVert} = u \\cdot v \\quad \\text{when } \\lVert u \\rVert = \\lVert v \\rVert = 1",
    interpretation: {
      tip: "On L2-normalized vectors the inner product is the cosine, so an exact inner-product search is an exact cosine search.",
    },
    pitfalls:
      "The index is rebuilt per engine build and capped at max_row_doc_rows vectors; it is a deterministic in-memory index, not a vector database.",
    diagram: "intro:cosine",
    links: [
      { label: "Borisov et al. 2022 — GReaT", url: "https://arxiv.org/abs/2210.06280", kind: "paper" },
      {
        label: "FAISS — index types",
        url: "https://github.com/facebookresearch/faiss/wiki/Faiss-indexes",
        kind: "docs",
      },
      L.adr0017,
      L.adr0019,
    ],
  },
  {
    id: "intro:generation-l4",
    title: "Generation on L4: vLLM, O(1) calls",
    purpose:
      "The model is asked what values a free-text column can take, once, and the rows are then sampled in NumPy without it. Each worker owns one vLLM server, started by the first model call and sized to the GPU it finds.",
    formula: "\\text{LLM calls} = O(\\#\\,\\text{free-text columns}),\\ \\text{not}\\ O(\\text{rows})",
    interpretation: {
      good: "A table with no free-text column, or a run whose pools are already persisted, never loads a model.",
      tip: "The pool cap (free_text_pool_max) bounds the model's work; the row count only moves CPU time.",
    },
    diagram: "intro:o1-calls",
    links: [
      L.design2,
      L.adr0013,
      L.adr0014,
      {
        label: "vLLM — structured outputs",
        url: "https://docs.vllm.ai/en/latest/usage/structured_outputs.html",
        kind: "docs",
      },
    ],
  },
  {
    id: "intro:mode-a",
    title: "Mode A guardrails",
    purpose:
      "The in-pipeline gates between the generator and the landing table: Pydantic per record, Pandera per batch, run-scoped uniqueness and primary keys, and foreign-key integrity. Every rejection lands in the dead-letter queue with its reason.",
    interpretation: {
      tip: "A thresholds gate runs after the load commits and compares the BLOCKER ratio with the environment's budget, so the audit row lands even when the run fails.",
    },
    pitfalls:
      "thresholds.yml declares fk.orphan a BLOCKER, but the gate's counted rule list omits it. The CONFIG tab shows what the code does.",
    diagram: "intro:mode-a",
    links: [
      L.design7,
      L.thresholds,
      { label: "Pydantic — validation", url: "https://docs.pydantic.dev/latest/concepts/models/", kind: "docs" },
      { label: "Pandera — DataFrame schemas", url: "https://pandera.readthedocs.io/en/stable/", kind: "docs" },
    ],
  },
  {
    id: "intro:write-landing",
    title: "WriteLanding (FILE_LOADS)",
    purpose:
      "Validated rows reach the landing table through BigQuery load jobs, not streaming inserts: the job is batch-shaped. The dead-letter queue and validation_runs are written the same way.",
    links: [
      L.design1,
      {
        label: "BigQuery — batch loading",
        url: "https://cloud.google.com/bigquery/docs/batch-loading-data",
        kind: "docs",
      },
      {
        label: "Beam — WriteToBigQuery",
        url: "https://beam.apache.org/releases/pydoc/current/apache_beam.io.gcp.bigquery.html",
        kind: "docs",
      },
    ],
  },
  {
    id: "intro:evaluation-job",
    title: "Evaluation (sdfb-evaluation)",
    purpose:
      "A standalone Beam job that measures what landed against its live source — fidelity, privacy, integrity and diversity — at every level from one field to the whole model. It never gates what is written; Mode A owns that.",
    interpretation: {
      tip: "Scores run from 0 to 1, higher is better. Read a change against the metric's noise floor before trusting it.",
    },
    links: [
      L.catalogue,
      L.evalDesign,
      {
        label: "sdfb-evaluation README",
        url: `${REPO}/packages/sdfb-evaluation/README.md`,
        kind: "docs",
      },
    ],
  },
  {
    id: "intro:registry",
    title: "Evaluation registry",
    purpose:
      "evaluation_data_history is append-only: an evaluation writes a RUNNING event, then a FINAL one. The evaluation_latest view keeps the last event per evaluation_id.",
    interpretation: {
      tip: "A FINAL row carries the family and overall scores; a RUNNING row has none yet.",
    },
    links: [L.registrySql, L.evalDesign],
  },

  // ----------------------------------------------------------------- counters
  {
    id: "intro:counter-runs",
    title: "Generation runs",
    purpose:
      "Rows of synthetic_data_quality.validation_runs: one per table per launch, written even when the run fails. A five-table launch adds five.",
    links: [L.design7],
  },
  {
    id: "intro:counter-evaluations",
    title: "Evaluations",
    purpose: "Distinct evaluation ids in the registry (evaluation_data_history), running ones included.",
    links: [L.registrySql],
  },
  {
    id: "intro:counter-tables",
    title: "Tables evaluated",
    purpose:
      "Distinct table names across the relationship models of every evaluation in the registry, external parents included.",
    links: [L.registrySql, L.adr0032],
  },

  // ---------------------------------------------------------- how it works
  {
    id: "intro:claim",
    title: "Claim line",
    purpose:
      "Each DESIGN.md section opens with one bold sentence that its figure and code must back. The cards quote it verbatim; a section without one shows its lead sentence, labelled as such.",
    interpretation: {
      tip: "A stale figure is worse than none: every card links the section it quotes, so the claim can be checked at its source.",
    },
    links: [{ label: "DESIGN.md", url: DESIGN, kind: "docs" }, L.adr0007],
  },
  {
    id: "intro:figure-provenance",
    title: "Figure provenance",
    purpose:
      "Every figure here is copied from the repository by npm run assets:sync, with its source path, SHA-256, generating script and owning document. A measured number is typed once, in its script's MEASURED block.",
    links: [L.design10, { label: "assets-sync.mjs", url: `${REPO}/gui/scripts/assets-sync.mjs`, kind: "code" }],
  },
  {
    id: "intro:adr",
    title: "ADR (architecture decision record)",
    purpose:
      "A numbered record of one decision, with the alternatives it rejected and the run that motivated it. Code comments cite the number, and DESIGN.md §9 maps each number to its section.",
    links: [
      {
        label: "Nygard 2011 — documenting architecture decisions",
        url: "https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions",
        kind: "docs",
      },
      L.design9,
      { label: "docs/adr/README.md", url: `${REPO}/docs/adr/README.md`, kind: "adr" },
    ],
  },
  {
    id: "intro:import-direction",
    title: "Import direction",
    purpose:
      "sdfb-beam imports sdfb-core and never the other way, so an engine is testable with no Beam, no GPU and no network. sdfb-evaluation imports neither and runs as its own job; the GUI imports no Python and only reads tables.",
    interpretation: {
      tip: "Solid arrows are imports; dashed arrows are data a package writes or reads, or contracts the GUI generates from the Python side.",
    },
    links: [
      L.adr0006,
      {
        label: "sdfb-evaluation independence test",
        url: `${REPO}/packages/sdfb-evaluation/tests/unit/test_independence.py`,
        kind: "code",
      },
      L.adr0042,
    ],
  },

  // ----------------------------------------------------- relationship model
  {
    id: "intro:relationship-model",
    title: "Relationship model",
    purpose:
      "Versioned YAML in config/relationships/ declares tables, keys and foreign-key edges, with enabled, enforced and drives flags. It is the only source of relational structure; table descriptions are never read for it.",
    interpretation: {
      tip: "Nothing beyond the columns is declared for a shape: every edge's role is derived from the model's own graph.",
    },
    links: [L.adr0032, L.relReadme, L.adr0037],
  },
  {
    id: "intro:role-driving",
    title: "Driving edge",
    purpose:
      "The one edge a child is generated from: its parent's landed keys become the child's request stream, so the ratio, key uniqueness and referential integrity hold by construction. It is the edge marked drives: true, else the first declared, and the launcher says which.",
    diagram: roleDiagram,
    links: [L.adr0036, L.adr0037, L.design4],
  },
  {
    id: "intro:role-implied",
    title: "Implied edge",
    purpose:
      "A parent the driving parent already reaches: its columns are copied out of the driving key tuple. It moves no data, so the Dataflow job graph draws no arrow for it.",
    diagram: roleDiagram,
    links: [L.adr0037, L.relReadme],
  },
  {
    id: "intro:role-independent",
    title: "Independent edge",
    purpose:
      "A second parent with no column in common with the driving edge. Its key is drawn as a whole tuple from the parent's sampled key pool.",
    diagram: roleDiagram,
    links: [L.adr0037, L.adr0031],
  },
  {
    id: "intro:role-conditional",
    title: "Conditional edge",
    purpose:
      "A second parent sharing a column with the driving edge: the shared value comes from the driving key, and the rest is a candidate that exists in that parent for it. Each shared value keeps at most fk_candidate_cap candidates.",
    diagram: roleDiagram,
    links: [L.adr0037, L.starDiamond],
  },
  {
    id: "intro:role-external",
    title: "External edge",
    purpose:
      "A parent outside the model: a dataset-qualified table that is already landed. The launch never generates it and draws keys from a sampled pool of it.",
    links: [L.adr0037, L.adr0031],
  },
  {
    id: "intro:role-documented",
    title: "Documented edge",
    purpose:
      "An edge declared with enforced: false: the table stays and no keys are drawn through the edge. The evaluator reports its orphan rate as INFO, next to the source's own orphan rate.",
    links: [L.adr0032, L.relReadme],
  },
  {
    id: "intro:role-disabled",
    title: "Disabled edge",
    purpose:
      "An edge of a table declared enabled: false. The table leaves the graph, with anything that reached the model only through it.",
    links: [L.adr0032, L.relReadme],
  },
  {
    id: "intro:shape-star",
    title: "Star",
    purpose:
      "A fact under two unrelated dimensions. One dimension drives the fact; the other shares no column with it, so it is independent and drawn as a whole key tuple.",
    links: [L.starDiamond, L.adr0037, L.shapesTest],
  },
  {
    id: "intro:shape-diamond",
    title: "Diamond",
    purpose:
      "Two branches rejoining under a shared ancestor. The bottom table is driven by one branch and conditional on the other through the ancestor's key.",
    links: [L.starDiamond, L.adr0037, L.shapesTest],
  },
  {
    id: "intro:shape-tree",
    title: "Tree",
    purpose: "One parent per child, branching below a root. It is the common case and needs no flags at all.",
    links: [L.adr0036, L.shapesTest],
  },
  {
    id: "intro:shape-chain",
    title: "Chain",
    purpose:
      "Each table has one parent one level up, such as accounts, orders and order lines. A chain is 1:1 when the child's primary key is the edge itself.",
    links: [L.retail, L.adr0036, L.shapesTest],
  },
  {
    id: "intro:shape-forest",
    title: "Forest",
    purpose:
      "Independent components in one model, each generated from its own root in the same job. One component may be a 1:1 chain.",
    links: [L.adr0030, L.adr0037, L.shapesTest],
  },
  {
    id: "intro:shape-graph",
    title: "Graph",
    purpose:
      "An arbitrary foreign-key graph: a child that reaches both its parent and its grandparent, plus a parent outside the model. thelook's order_items is one.",
    links: [L.thelook, L.adr0037, L.shapesTest],
  },
]);
