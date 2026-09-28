/**
 * CONFIG tab concepts (namespaces `knob:`, `config:`, `stats:`) and the knob
 * guides behind every knob sheet.
 *
 * - `knobGuides`: one entry per knobs.json knob (typed by `KnobId`, so a new
 *   knob fails the typecheck until it has a guide): purpose, what turning it
 *   up or down does, and a practical tip. Values, defaults and sources are
 *   never typed here — they come from generated/knobs.json.
 * - `knob:<id>` concepts are derived from the guides plus the knob's code,
 *   ADR and doc links (at the commit the export ran on).
 * - `stats:` and `config:` concepts explain the maths and the guardrails,
 *   each with its primary sources.
 */
import { knobs as knobsFile, type KnobId } from "../../generated/knobs";
import { defineConcepts, type Concept, type ConceptLink } from "../concept";

const REPO_URL = "https://github.com/albertols/synthetic-llm-dataflow-bigquery";
/** Links resolve at the commit knobs.json was exported from (clean export), else master. */
export const CONFIG_LINK_REF =
  knobsFile.exported_from.commit && knobsFile.exported_from.dirty.length === 0
    ? knobsFile.exported_from.commit
    : "master";
const blob = (path: string, line?: number) => `${REPO_URL}/blob/${CONFIG_LINK_REF}/${path}${line ? `#L${line}` : ""}`;

/** The ADRs knobs.json and this tab cite: number → file and short title (docs/adr/). */
export const ADRS: Readonly<Record<string, { file: string; title: string }>> = {
  "0005": { file: "0005-live-select-reference-data.md", title: "Live BQ SELECT for reference rows" },
  "0006": { file: "0006-generation-engine-abc.md", title: "GenerationEngine ABC + ModelClient Protocol" },
  "0013": { file: "0013-distribution-estimator-spine.md", title: "LLM as distribution estimator, not per row" },
  "0014": { file: "0014-vllm-model-client-owns-server.md", title: "VLLMModelClient owns the vLLM server" },
  "0017": { file: "0017-custom-rag-layer-over-beam-ml-rag.md", title: "Custom RAG layer" },
  "0018": { file: "0018-parallel-batched-freetext-pools.md", title: "Batched, parallel, cached free-text pools" },
  "0019": { file: "0019-rag-population-scoped-to-consumers.md", title: "RAG population scoped to consumers" },
  "0020": { file: "0020-freetext-pools-as-persisted-artifact.md", title: "Free-text pools are a persisted artifact" },
  "0022": { file: "0022-stats-driven-generation.md", title: "source_table_stats as a generation input" },
  "0023": { file: "0023-source-domain-pool-rejection.md", title: "Pools reject against the full source domain" },
  "0024": { file: "0024-structured-prompt-constraint-templates.md", title: "Structured prompt-constraint templates" },
  "0025": { file: "0025-marginal-fidelity-by-construction.md", title: "Marginal fidelity by construction" },
  "0028": { file: "0028-constraint-router-relational-plan.md", title: "Constraint router + relational plan" },
  "0029": { file: "0029-fk-model-scenarios-and-history-mappings.md", title: "FK-model generation scenarios" },
  "0030": { file: "0030-single-job-relational-generation.md", title: "Single-job relational generation" },
  "0031": { file: "0031-joint-fk-key-draws.md", title: "Referential integrity by construction" },
  "0032": { file: "0032-relationships-as-config.md", title: "Relationships are config" },
  "0033": { file: "0033-pool-ladder-integrity-at-scale.md", title: "Pool-ladder integrity at scale" },
  "0034": {
    file: "0034-generation-throughput-single-barrier-shared-engines.md",
    title: "One dedup barrier, one engine per process",
  },
  "0035": { file: "0035-pk-capacity-fk-bound-members.md", title: "PK capacity counts FK-bound members" },
  "0036": { file: "0036-parent-driven-fanout-generation.md", title: "Parent-driven fan-out generation" },
  "0037": { file: "0037-multi-parent-children.md", title: "Multi-parent children" },
  "0038": { file: "0038-measured-conflicts-adjust-the-model.md", title: "Measured conflicts adjust the model" },
};

export const adrLink = (number: string): ConceptLink | null => {
  const adr = ADRS[number];
  return adr ? { label: `ADR ${number} — ${adr.title}`, url: blob(`docs/adr/${adr.file}`), kind: "adr" } : null;
};

const SCALING = "docs/designs/2026-07-24-reference-sample-scaling.md";
const STATS_DOC = "docs/designs/2026-08-05-source-table-stats.md";
const doc = (label: string, path: string, line?: number): ConceptLink => ({
  label,
  url: blob(path, line),
  kind: "docs",
});
const code = (label: string, path: string, line?: number): ConceptLink => ({
  label,
  url: blob(path, line),
  kind: "code",
});
const paper = (label: string, url: string): ConceptLink => ({ label, url, kind: "paper" });
const adr = (number: string): ConceptLink => adrLink(number)!;

/** Code constants the copy quotes, read from knobs.json (never retyped). */
function knobNumber(id: KnobId): number {
  const value = knobsFile.knobs.find((k) => k.id === id)?.value;
  if (typeof value !== "number") throw new Error(`knobs.json: ${id} is not numeric`);
  return value;
}
const fmt = (v: number) => v.toLocaleString("en-US");
const POOL_CAP = knobNumber("free_text_pool_max");
const ROW_DOCS = knobNumber("max_row_doc_rows");
const TOP_K = knobNumber("rag_top_k");
const N_DEFAULT = knobNumber("reference_rows_limit");
const MARGIN = knobNumber("fk_key_sample_margin");
const SOURCE_CAP = knobNumber("source_domain_cap");
/** DKW ε at the default n, α = 0.05. */
const EPS_DEFAULT = Math.sqrt(Math.log(40) / (2 * N_DEFAULT)).toFixed(4);
/** Duplicate share of M uniform draws from K = MARGIN·M: 1 − MARGIN·(1 − e^(−1/MARGIN)). */
const DUP_AT_MARGIN = `${((1 - MARGIN * -Math.expm1(-1 / MARGIN)) * 100).toFixed(1)} %`;
const TEX_POOL_CAP = fmt(POOL_CAP).replace(/,/g, "{,}");

const P = {
  dkw: paper("Dvoretzky, Kiefer & Wolfowitz 1956 — the DKW inequality", "https://doi.org/10.1214/aoms/1177728174"),
  massart: paper("Massart 1990 — the tight DKW constant", "https://doi.org/10.1214/aop/1176990746"),
  shannon: paper(
    "Shannon 1948 — A mathematical theory of communication",
    "https://ieeexplore.ieee.org/document/6773024",
  ),
  hll: paper("Heule, Nunkesser & Hall 2013 — HyperLogLog++", "https://research.google.com/pubs/archive/40671.pdf"),
  wilson: paper("Wilson 1927 — the score interval", "https://doi.org/10.1080/01621459.1927.10502953"),
  good: paper(
    "Good 1953 — population frequencies of species (Good–Turing)",
    "https://doi.org/10.1093/biomet/40.3-4.237",
  ),
  devroye: paper("Devroye 1986 — Non-Uniform Random Variate Generation", "https://doi.org/10.1007/978-1-4613-8643-8"),
  hurlbert: paper("Hurlbert 1971 — rarefaction", "https://doi.org/10.2307/1934145"),
  carlini: paper("Carlini et al. 2021 — extracting training data", "https://arxiv.org/abs/2012.07805"),
  bqHll: {
    label: "BigQuery HLL++ functions",
    url: "https://cloud.google.com/bigquery/docs/reference/standard-sql/hll_functions",
    kind: "docs",
  } as ConceptLink,
  bqApprox: {
    label: "BigQuery approximate aggregate functions",
    url: "https://cloud.google.com/bigquery/docs/reference/standard-sql/approximate_aggregate_functions",
    kind: "docs",
  } as ConceptLink,
  vllmApc: {
    label: "vLLM automatic prefix caching",
    url: "https://docs.vllm.ai/en/stable/design/prefix_caching/",
    kind: "docs",
  } as ConceptLink,
  pydantic: { label: "Pydantic v2", url: "https://docs.pydantic.dev/latest/", kind: "docs" } as ConceptLink,
  pandera: { label: "Pandera", url: "https://pandera.readthedocs.io/en/stable/", kind: "docs" } as ConceptLink,
  beamDlq: {
    label: "Apache Beam — BigQueryIO patterns (DLQ)",
    url: "https://beam.apache.org/documentation/patterns/bigqueryio/",
    kind: "docs",
  } as ConceptLink,
  dataflowQuotas: {
    label: "Dataflow quotas and limits",
    url: "https://cloud.google.com/dataflow/quotas",
    kind: "docs",
  } as ConceptLink,
};

// ------------------------------------------------------------ knob guides --

export interface KnobGuide {
  /** At most two sentences: what the knob is for. */
  purpose: string;
  /** What raising it (or moving it to the "more" end of its choices) does. */
  up: string;
  /** What lowering it does. */
  down: string;
  tip?: string;
  /** KaTeX: the maths the knob enters. */
  formula?: string;
  /** Further reading beyond the knob's own ADRs and docs. */
  refs?: ConceptLink[];
}

const CODE_EDIT = "A constant: changing it is a code edit, a test run and an image rebuild.";

export const knobGuides: Readonly<Record<KnobId, KnobGuide>> = {
  // ---------------------------------------------------------------- SAMPLING
  reference_rows_limit: {
    purpose:
      "How many source rows the generator reads as its reference sample (n). Every profile, pool seed and validation baseline is estimated from these rows.",
    up: "A smaller DKW band (four times the rows per halving), better tails and rare categories. A new digest makes the run cold: every pool and chunk rebuilds, and driver memory and profiling time grow linearly.",
    down: "A cheaper sample query and a smaller leak surface, but rare categories and tails vanish first. Cardinality stays truncated at n either way.",
    tip: "Raise it when a tail or a rare category matters and expect a cold run; answer cardinality with --source_stats exact, never with a bigger sample.",
    formula: `\\varepsilon(n) = \\sqrt{\\frac{\\ln(2/\\alpha)}{2n}},\\quad \\varepsilon(${fmt(N_DEFAULT).replace(/,/g, "{,}")}) \\approx ${EPS_DEFAULT}`,
    refs: [
      P.dkw,
      P.massart,
      doc(
        "Article 2 — the 10k sample, its knobs and their aftermath",
        "docs/articles/02-type-system-freetext-resolution.md",
        262,
      ),
    ],
  },
  source_stats: {
    purpose:
      "Which source-stats tier the profiler writes: off, sample (the reference sample, free) or exact (one aggregate scan of the live table).",
    up: "exact adds HLL++ distinct, deciles and top-k over the whole table, and the exact distinct count sizes the free-text pool target. One BigQuery scan of the source per run.",
    down: "off writes no stats rows, no JSON and no pool hints; engines still profile per worker. sample is free, but every distinct count is capped at the rows the sample saw.",
    tip: "A failed exact scan degrades loudly to the sample tier (milestone source_stats_exact_failed) and never kills the run.",
    refs: [P.hll, P.bqApprox],
  },
  profiler_version: {
    purpose: "The version of profile_source_table, part of the stats table's append-skip key.",
    up: "Bumping it re-profiles every table on its next run, even when the reference digest is unchanged.",
    down: "Never lower it: tables already profiled at a newer version keep their rows, and stale ones stay stale.",
    tip: CODE_EDIT,
  },
  top_values_max_distinct: {
    purpose:
      "Literal source values land in the stats only for enum-like columns with at most this many distinct values. Above it a column gets shapes, entropy and lengths, never values.",
    up: "More columns store literal values in the stats table: the exemplar-leak surface grows.",
    down: "Fewer literal values; more columns are described by shape and entropy only.",
    tip: "It mirrors the engines' free-text category cap, so “enum-routed” means the same thing everywhere.",
    refs: [P.carlini],
  },
  top_values_top_k: {
    purpose: "How many literal top values an enum-like column keeps in its stats entry.",
    up: "More of the head of the distribution is visible in the stats row.",
    down: "A smaller stats row; the tail of an enum is summarised by entropy only.",
    tip: CODE_EDIT,
  },
  shape_mix_top_k: {
    purpose: "How many character-shape masks (9 digit, A upper, a lower) a column keeps with their shares.",
    up: "Rarer shapes of identifier-like columns stay visible to the shape expander and the drift checks.",
    down: "Only the dominant shapes survive; a long-tailed format loses its minority masks.",
    tip: CODE_EDIT,
  },
  length_percentiles: {
    purpose:
      "The length quantiles the profiler records per string column; the p05–p95 band becomes the pool prompt's length hint.",
    up: "A wider band admits more of the tail's lengths into prompts.",
    down: "A tighter band steers prompts toward typical lengths only.",
    tip: CODE_EDIT,
  },
  null_pattern_max_cols: {
    purpose: "Tables wider than this skip the row null-pattern pass, which costs O(rows × columns).",
    up: "Wide tables get their joint null structure measured, at a pass whose alphabet grows with width.",
    down: "More tables fall back to per-column null rates only.",
    tip: CODE_EDIT,
  },
  null_pattern_top_k: {
    purpose: "How many row null patterns (which columns are null together) the __table__ entry keeps.",
    up: "More of the joint null structure is recorded.",
    down: "Only the most common patterns survive.",
    tip: CODE_EDIT,
  },
  // -------------------------------------------------------------- GENERATION
  engine: {
    purpose:
      "Which GenerationEngine runs, by its ENGINE_REGISTRY name: b1_rag (retrieval plus bounded LLM pools) or b2_library (the sdgx wrapper).",
    up: "b2_library fits a library model once per worker and keeps more joint structure; numerics interpolate through 11 deciles.",
    down: "b1_rag samples marginals from the whole sorted sample and seeds LLM pools by retrieval.",
    tip: "Both engines sit behind one interface, so the pipeline, validation and audit are identical; swap with the flag.",
  },
  num_rows: {
    purpose:
      "Rows to generate (M) for a root table. A driven child derives its count from its parent's landed keys times the measured fan-out (ADR 0036).",
    up: "More rows reuse each pool value more often and tighten identifier keyspaces; time grows linearly.",
    down: "Faster runs; the fidelity per row does not change, because the sample, not M, sets the error.",
    tip: "M never enters the sampling-error formula: it only raises the cost of being wrong.",
    formula: `r_{\\text{pool}} = \\frac{M}{\\min(M,\\ D,\\ ${TEX_POOL_CAP})},\\qquad P_{\\text{collision}} \\approx 1 - e^{-M(M-1)/2K}`,
  },
  similarity: {
    purpose:
      "A 0–1 dial the engines interpret differently: b2 maps it to sampling temperatures, b1 only blends its temporal draws.",
    up: "b2: colder categorical sampling and a lower LLM temperature, output closer to the reference. b1: temporal draws anchored nearer observed instants.",
    down: "b2: hotter sampling, more divergence. b1: temporal draws closer to uniform over the range.",
    tip: "Numeric, categorical and free-text draws of b1 ignore it at the exported commit (see Docs differ).",
    formula: "T_{\\text{cat}} = 2(1 - s),\\qquad T_{\\text{LLM}} = 1.3 - 1.2\\,s",
  },
  seed: {
    purpose:
      "An explicit base RNG seed; empty derives one per (run_id, batch_id), so runs never replay across triggers.",
    up: "A fixed seed replays the same draws for the same run inputs — useful for A/B runs and goldens.",
    down: "Empty (default) keeps every trigger's draws independent.",
  },
  batch_size: {
    purpose:
      "Rows per Beam element. At the default it scales with --num_rows toward about 1,000 elements, never below 16.",
    up: "Fewer, larger elements amortise per-element Python overhead over vectorized draws; a failed batch loses more rows.",
    down: "More elements, more per-element overhead: 16 rows per element made a 1M-row run pay it 62,500 times.",
    tip: "Pass a value only to pin it; the default already scales.",
    formula: "b = \\max\\left(16,\\ \\left\\lfloor M / 1000 \\right\\rfloor\\right)",
  },
  generation_max_retries: {
    purpose:
      "GenerationConfig.max_retries, declared with a default of 2. No engine or DoFn reads it at the exported commit.",
    up: "No effect today.",
    down: "No effect today.",
    tip: "Pool retries follow the sampling ladder, and a failed batch goes to the DLQ as engine_failure; neither reads this field.",
  },
  temperature_ladder: {
    purpose:
      "The temperatures a free-text pool call escalates through when its novel yield is empty (every value a copy).",
    up: "A higher ceiling diversifies further away from exemplar echoes, at the cost of format and plausibility.",
    down: "Fewer escalation levels: an echo-saturated column gives up sooner.",
    tip: "Derived by escalating_temperatures(start); 1.3 is the ceiling b2 uses for maximum divergence.",
  },
  sampling_ladder: {
    purpose:
      "The (temperature, top_p, top_k) level of each pool attempt: the first keeps the model's own defaults, every retry escalates temperature with truncation unclamped.",
    up: "More levels give an echoing model more chances to leave the exemplar nucleus.",
    down: "Fewer levels: temperature alone cannot diversify a nucleus pinned by the model's generation_config.",
    tip: "none = the served model's default; top_k 0 means all tokens in vLLM.",
  },
  temporal_max_age_years_b1: {
    purpose:
      "b1 clamps the jitter floor of generated dates to now minus this many years; fully historical columns keep their observed range.",
    up: "Older dates are allowed again.",
    down: "A tighter window pushes generated instants toward the present.",
    tip: "Interim policy: per-column DDL-JSON descriptions will override the constant later.",
  },
  temporal_max_age_years_b2: {
    purpose:
      "b2's floor for generated dates and timestamps: max(observed min, now minus this many years). A column whose whole range is older keeps it.",
    up: "Older dates are allowed again.",
    down: "A tighter window; the decile vector's sub-floor points collapse onto the floor.",
    tip: "Interim policy: per-column DDL-JSON descriptions will override the constant later.",
  },
  // --------------------------------------------------------------- FREE TEXT
  free_text_pool_max: {
    purpose:
      "The most distinct values a free-text pool can hold, shared by both engines' ladders and the launcher's PK-capacity preflight. A PK routed to a pool caps at it.",
    up: "More diversity per column, at LLM cost linear in the pool size.",
    down: "Cheaper pools; M rows reuse each value M/cap times.",
    tip: "Identifiers never come from pools: shape expansion and keyspace generation carry them.",
    formula: `r = \\frac{M}{\\min(M,\\ D,\\ ${TEX_POOL_CAP})}\\ \\text{rows per value}`,
  },
  pool_values_per_call: {
    purpose: "Values requested per LLM call: one JSON-array completion.",
    up: "Fewer round trips per pool, longer completions per call.",
    down: "More round trips; each call is shorter and cheaper to retry.",
    tip: CODE_EDIT,
  },
  pool_parallel_choices: {
    purpose:
      "Independent completions (vLLM n=) per HTTP round trip, de-duplicated across choices. It multiplies novel yield per call about four times.",
    up: "More yield per round trip; more sequences in flight on the GPU.",
    down: "Less parallel sampling; more round trips.",
    tip: "Safe because pool requests are unseeded, and the prompt stays byte-identical (prefix-cache friendly).",
    refs: [P.vllmApc],
  },
  pool_stagnation_window: {
    purpose: "Once every escalation level has run, this many consecutive low-yield attempts end a pool ladder.",
    up: "Ladders keep trying longer against decaying yield.",
    down: "Ladders stop sooner; a slow-but-productive column may end undersized.",
    tip: CODE_EDIT,
  },
  pool_stagnation_min_novel: {
    purpose: "An attempt that adds fewer novel values than this counts toward stagnation.",
    up: "Stricter: more attempts count as stagnant, ladders end sooner.",
    down: "Looser: a trickle of novel values keeps a ladder alive.",
    tip: CODE_EDIT,
  },
  pool_format_collapse_rounds: {
    purpose:
      "Consecutive full-yield rounds with zero in-format values that end a ladder (a structural mismatch temperature cannot fix).",
    up: "More wasted rounds before the shape fallback takes over.",
    down: "Faster exit to the shape fallback, less tolerance for a noisy first round.",
    tip: CODE_EDIT,
  },
  pool_build_max_workers: {
    purpose:
      "How many column ladders run in parallel on a thread pool; vLLM continuous-batches the concurrent requests.",
    up: "More concurrency, until the GPU's KV cache queues the extra sequences.",
    down: "Closer to sequential ladders: setup time grows with the number of free-text columns.",
    tip: CODE_EDIT,
  },
  source_domain_cap: {
    purpose:
      "The largest source-distinct set kept as a worker-side rejection filter against copies. Above it the engine degrades loudly to sample-only rejection.",
    up: "High-cardinality columns keep full-domain copy rejection, at tens of MB of worker memory per million strings.",
    down: "More columns fall back to rejecting only what the sample saw.",
    tip: CODE_EDIT,
  },
  free_text_unique_ratio: {
    purpose:
      "A string column whose unique share exceeds this, with a long mean length, routes to free text instead of categorical.",
    up: "Fewer columns reach the LLM pool.",
    down: "More columns reach the LLM pool, including repetitive ones.",
    tip: CODE_EDIT,
  },
  free_text_min_mean_len: {
    purpose: "The mean rendered length above which a high-unique string column is free text.",
    up: "Short unique strings stay categorical or shaped.",
    down: "Short unique strings route to the pool.",
    tip: CODE_EDIT,
  },
  free_text_max_categories: {
    purpose: "Above this distinct count a string column is high-cardinality and treated as free text even if short.",
    up: "More columns stay categorical (sampled from their observed values).",
    down: "More columns become free text or shaped identifiers.",
    tip: "The profiler's literal top-values cap mirrors it.",
  },
  freetext_expansion: {
    purpose:
      "The shape-preserving expander after pool draws: off (pool draws only), identifiers (code-like columns expand from their shape mix) or all.",
    up: "all also mutates digit runs inside texty draws: more distinct values, never an LLM call.",
    down: "off caps every free-text column's distinct count at the pool size.",
    tip: `The default, identifiers, is what keeps a 90M-row run from repeating ${fmt(POOL_CAP)} codes.`,
  },
  pool_seed_strategy: {
    purpose: `How the ${TOP_K} prompt seeds are chosen: centroid (densest region), kcenter (spans the column's modes) or kcenter_rotate (re-seeded per attempt).`,
    up: "kcenter and kcenter_rotate widen coverage of rare modes; rotate forfeits vLLM prefix caching by design.",
    down: "centroid (control) keeps a byte-identical prefix and conditions on the densest region.",
    refs: [P.vllmApc],
  },
  prompt_constraints: {
    purpose:
      "Attach each column's llm_prompt_constraint and measured length band to pool prompts, as a constant suffix.",
    up: "on: prompts follow the declared format; the shared prefix stays cacheable.",
    down: "off: prompts rely on exemplars alone.",
  },
  prompt_debug: {
    purpose: "Log each built pool prompt as a milestone: off, redacted (seed exemplars elided) or full.",
    up: "full logs verbatim prompts at WARNING: reference values reach Dataflow logs. Debug runs only.",
    down: "off logs nothing about prompts.",
  },
  pool_pattern_guidance: {
    purpose:
      "Constrain identifier-ish pool completions at decode time with a charset and length regex (vLLM structured output).",
    up: "true: fewer off-format values reach the post-hoc gate, at an unmeasured T4 throughput cost.",
    down: "empty or false (default): the post-hoc format gate alone protects pools.",
  },
  build_pool_layer: {
    purpose:
      "Build free-text pools in their own branch and persist them (skipped when this reference digest and model are already stored).",
    up: "true: pools are built once and every worker reads them (warm runs skip the LLM entirely).",
    down: "empty: pools are inferred inside every worker process's setup().",
    tip: "The Composer DAG defaults it to true.",
  },
  // --------------------------------------------------------------------- RAG
  max_row_doc_rows: {
    purpose:
      "How many fingerprint-ordered reference rows are embedded as row documents: exactly the rows the engine reads back.",
    up: "More rows in the in-worker index; population cost grows, and rows past the engine's read contract would be unreadable.",
    down: "A smaller index that covers fewer of the sample's modes.",
    tip: "The engine's _MAX_EMBED_ROWS aliases it: one write/read contract.",
  },
  max_free_text_values_per_column: {
    purpose: "Distinct (column, value) chunks embedded per free-text column; a near-unique column stays bounded here.",
    up: `More candidates for the top-${TOP_K} exemplar pick, more embedding work.`,
    down: "Fewer candidates; rare values are less likely to be seeds.",
    tip: CODE_EDIT,
  },
  rag_top_k: {
    purpose: "Exemplars retrieved to seed each free-text pool prompt.",
    up: "Longer prompts with more context; exemplar echoes become more likely.",
    down: "Shorter prompts, less diversity to condition on.",
    tip: CODE_EDIT,
  },
  hashing_embedder_dim: {
    purpose: "Dimension of the laptop HashingEmbedder (feature hashing, no model weights).",
    up: "Fewer hash collisions between tokens, more memory per vector.",
    down: "More collisions; neighbours blur.",
    tip: CODE_EDIT,
  },
  bge_embedder_dim: {
    purpose: "Output dimension of bge-small-en-v1.5, fixed by the model.",
    up: "Fixed by the checkpoint.",
    down: "Fixed by the checkpoint.",
  },
  bge_max_length: {
    purpose: "Tokens bge-small reads per chunk; longer serialized rows are truncated.",
    up: "Fixed by the checkpoint's position embeddings.",
    down: "Truncates more of each serialized row.",
  },
  embedder_min_free_vram: {
    purpose: "Free VRAM required before the embedder's auto device picks CUDA over CPU.",
    up: "The embedder yields the GPU more often (safer next to vLLM).",
    down: "More CUDA embeds, and a real OOM risk when the card is already spoken for.",
    tip: "cuda.is_available() answers “does a GPU exist”, never “is there room”.",
  },
  default_embedder_identity: {
    purpose: "The embedder name and version used when --embedder_uri is empty: the HashingEmbedder.",
    up: "A constant identity: chunks are keyed by it.",
    down: "A constant identity: chunks are keyed by it.",
  },
  embedder_uri: {
    purpose: "gs:// path of the B.1 RAG embedder's weights; empty uses the HashingEmbedder.",
    up: "Setting it loads bge-small from GCS (never the Hub) for semantic retrieval.",
    down: "Empty: hashing vectors, no model download, lexical neighbours only.",
  },
  build_rag_layer: {
    purpose:
      "Populate synthetic_rag.rag_chunks from this run's reference sample (skipped when the digest is already present for this embedder).",
    up: "true: chunks and vectors persist, and later runs read them instead of re-embedding.",
    down: `empty: the engine embeds its ${fmt(ROW_DOCS)} rows in setup on every fresh digest.`,
    tip: "The Composer DAG defaults it to true.",
  },
  // -------------------------------------------------------------- GUARDRAILS
  env: {
    purpose: "The threshold tier (dev, uat, prd) that selects the BLOCKER ratio a run may reach before it fails.",
    up: "Toward prd: a stricter gate (1 %).",
    down: "Toward dev: a looser gate (20 %).",
    formula: "\\text{FAILED} \\iff \\frac{\\text{blocker rows}}{\\text{valid} + \\text{DLQ}} > r_{\\text{env}}",
  },
  uniqueness_mode: {
    purpose:
      "How duplicates are handled before landing: exact (one full-row shuffle barrier), exact_chained (the older three-barrier chain) or streaming (measure, do not remove).",
    up: "exact_chained: three shuffles per table, kept for A/B runs only.",
    down: "streaming: no barrier between generation and BigQuery; duplicate rows land and the run still fails the gate.",
    tip: "exact is the default and removes every duplicate behind one barrier.",
  },
  driven_uniqueness_mode: {
    purpose:
      "The uniqueness mode for a driven child without identity columns: its PK is unique by construction, so streaming measures without a barrier.",
    up: "exact adds a barrier the construction already made unnecessary.",
    down: "streaming (default) lands rows without the landing-path shuffle.",
  },
  write_disposition: {
    purpose: "Landing-table write mode: append (WRITE_APPEND) or overwrite (WRITE_TRUNCATE).",
    up: "overwrite replaces the table: the re-run after a streaming-mode failure.",
    down: "append adds rows; the DLQ, validation_runs and rag_chunks always append.",
  },
  blocker_failure_ratio_dev: {
    purpose: "The share of rows failing a BLOCKER rule a dev run may reach before the job fails.",
    up: "Looser: more defective rows are tolerated.",
    down: "Stricter: fewer defective rows before FAILED_BLOCKER.",
    tip: "Declared in config/thresholds.yml, not in code.",
  },
  blocker_failure_ratio_uat: {
    purpose: "The share of rows failing a BLOCKER rule a uat run may reach before the job fails.",
    up: "Looser: more defective rows are tolerated.",
    down: "Stricter: fewer defective rows before FAILED_BLOCKER.",
    tip: "Declared in config/thresholds.yml, not in code.",
  },
  blocker_failure_ratio_prd: {
    purpose: "The share of rows failing a BLOCKER rule a prd run may reach before the job fails.",
    up: "Looser: more defective rows are tolerated.",
    down: "Stricter: fewer defective rows before FAILED_BLOCKER.",
    tip: "Declared in config/thresholds.yml, not in code.",
  },
  blocker_rules_declared: {
    purpose: "The rule ids config/thresholds.yml declares with severity BLOCKER.",
    up: "Declaring a rule BLOCKER documents intent; only BLOCKER_RULE_IDS makes the gate count it.",
    down: "Removing one changes the contract, not the gate.",
  },
  blocker_rule_ids: {
    purpose: "The rule ids whose DLQ rows the BLOCKER gate counts (BLOCKER_RULE_IDS in validation/summary.py).",
    up: "Counting more rules fails more runs.",
    down: "A rule left out diverts rows to the DLQ without ever moving the gate.",
    tip: "engine_failure is weighted by the lost batch size, not one per envelope.",
  },
  repeat_share_tolerance: {
    purpose:
      "The absolute tolerance between the source's key-repeat share and the landed one, when a launch adjusted the model (ADR 0038).",
    up: "Looser verdicts on an adjusted table's copy.",
    down: "Tighter: per-key sampling noise (O(1/√keys)) may flag a faithful copy.",
  },
  // -------------------------------------------------------------- RELATIONAL
  generate_fk_relationships: {
    purpose:
      "Honour declared relationships: a launch expands to the table's whole FK component, parents first, and children sample landed parent keys.",
    up: "true (default): referential integrity by construction.",
    down: "false: declared edges are ignored loudly and FK columns use marginals.",
  },
  relationships_uri: {
    purpose:
      "Where the relationship models live: a folder or YAML file, local or gs://. The only source of relational truth.",
    up: "A gs:// path changes PK/FK without rebuilding the image.",
    down: "The default reads config/relationships packaged in the image.",
  },
  multi_table_mode: {
    purpose:
      "How a multi-table plan runs: single_job (one fleet, one vLLM ignition, in-DAG key handoff) or sequential_jobs.",
    up: "sequential_jobs: one job per table, parents first — a fallback for debugging.",
    down: "single_job (default): one worker fleet for the whole component.",
  },
  on_model_conflict: {
    purpose:
      "What a measured contradiction between the relationship model and the source does: adjust (drop the disproved PK and announce it) or stop.",
    up: "stop refuses the launch, as before ADR 0038.",
    down: "adjust (default) carries on and reproduces the source's key-repeat share.",
    tip: "Self-contradictions and the PK-capacity gate stop under both settings.",
  },
  fk_candidate_cap: {
    purpose: "Top-M parent candidates kept per shared value on a conditional (diamond) edge.",
    up: "A wider per-key choice, and fewer keys per batch to keep a request under the value cap.",
    down: "Hot shared keys wrap sooner over a shorter candidate list.",
    formula: "\\text{keys per batch} \\le \\left\\lfloor \\frac{100{,}000}{M_{\\text{cap}}} \\right\\rfloor",
  },
  fk_key_sample_floor: {
    purpose: "The side-input cap: a child samples at most this many parent key tuples unless its PK needs more.",
    up: "Bigger side inputs per worker, fewer duplicate draws.",
    down: "Smaller side inputs; PKs built on the key draw collide sooner.",
    tip: CODE_EDIT,
  },
  fk_key_sample_ceiling: {
    purpose:
      "The largest parent-key side input the sizing will ask for (about 40 MB pickled per million single-column keys).",
    up: "More SDK memory per process; past it the answer is the co-partitioned join (ADR 0036).",
    down: "PK capacity runs out sooner on big children.",
    tip: CODE_EDIT,
  },
  fk_key_sample_margin: {
    purpose: `The capacity target over num_rows when PK members are drawn at random: at ${MARGIN}×, expected duplicates are about ${DUP_AT_MARGIN}.`,
    up: "Fewer duplicate draws, larger side inputs.",
    down: "More pk.duplicate rows diverted to the DLQ.",
    formula: `\\text{dup share} = 1 - \\frac{K}{M}\\left(1 - e^{-M/K}\\right),\\quad K = ${MARGIN} \\cdot M \\Rightarrow ${DUP_AT_MARGIN.replace(" %", "\\%")}`,
  },
  max_conditional_values_per_request: {
    purpose:
      "The most candidate values one fan-out request may carry per conditional edge; keys per batch shrink to stay under it.",
    up: "Bigger requests and fewer elements.",
    down: "Smaller requests; more elements for the same keys.",
    tip: CODE_EDIT,
  },
  // ----------------------------------------------------------------- SERVING
  client_type: {
    purpose:
      "Which ModelClient generates: vllm (a server the worker owns), mlx (local Apple smoke) or fake (no model).",
    up: "vllm: real free-text pools on a GPU worker.",
    down: "fake: exercises the whole Dataflow → BigQuery path without a GPU.",
  },
  vllm_dtype: {
    purpose: "vLLM's --dtype: auto (the checkpoint's), float16 or bfloat16.",
    up: "bfloat16 needs an L4-class GPU.",
    down: "float16 is required on T4 for bf16 checkpoints like Qwen; refused for Gemma (empty output).",
    tip: "The Composer DAG defaults it to float16.",
  },
  vllm_max_model_len: {
    purpose: "vLLM's --max-model-len cap; without it the KV cache is sized for the checkpoint's native context.",
    up: "Longer prompts fit, and the KV cache takes more VRAM (262k native context killed a T4 at startup).",
    down: "Less VRAM for KV; 8,192 already dwarfs the synthesis prompts.",
  },
  autoscaling: {
    purpose:
      "Fleet sizing: auto (pinned when --initial_workers is set), fixed (pinned, workers required) or throughput (Dataflow scales).",
    up: "throughput: Dataflow scales freely; multi runs lost about 4 min per job to mid-job scale-downs.",
    down: "fixed: a fleet that starts and stays full.",
  },
  initial_workers: {
    purpose: "The initial Dataflow worker count; empty keeps Dataflow's own default.",
    up: "A scale run starts at its max_num_workers and skips the ramp.",
    down: "Empty: the R6 pair started on 2 workers and ran 8 min at a quarter of its steady rate.",
  },
  gpu: {
    purpose: "The Composer DAG's GPU profile (l4 or t4), resolved at deploy time from the {{GPU}} marker.",
    up: "l4: bfloat16 and larger models fit.",
    down: "t4: cheaper, float16 only, tighter VRAM.",
  },
  sdk_containers: {
    purpose:
      "SDK-container topology for vLLM launches: single (one Python process per worker) or multi (one per vCPU).",
    up: "multi: generation escapes the one-interpreter GIL ceiling; the sheet charts the measured pair.",
    down: "single (default): one process owns the GPU; generation is bound by one interpreter.",
  },
  // -------------------------------------------------------------- EVALUATION
  eval_mode: {
    purpose: "Planned evaluator flag: exact scans every row; sampled evaluates Bernoulli samples per side.",
    up: "exact: no sampling error on the synthetic side, full-table cost.",
    down: "sampled: cheaper, with intervals as wide as the sample.",
  },
  eval_sample_rows: {
    purpose: "Planned: rows per side in sampled mode.",
    up: "Tighter noise floors (1/√n), more bytes scanned.",
    down: "Cheaper, wider floors.",
    formula: "\\varepsilon(2\\times10^5) \\approx 0.0030",
  },
  eval_privacy_sample_rows: {
    purpose: "Planned: synthetic rows the Gower nearest-neighbour privacy checks read.",
    up: "Rarer copies become detectable; nearest-neighbour cost grows quadratically.",
    down: "Faster, less sensitive privacy checks.",
  },
  eval_detection_sample_rows: {
    purpose: "Planned: rows per side the classifier two-sample test reads.",
    up: "A tighter AUC interval.",
    down: "A wider AUC interval.",
  },
  eval_pair_max_columns: {
    purpose: "Planned: columns whose pairs feed the correlation and contingency metrics.",
    up: "Quadratically more pairs.",
    down: "Joint structure checked on fewer columns.",
  },
  eval_topk_profile: {
    purpose: "Planned: values kept in each top-k profile (metrics still use every value).",
    up: "Bigger profile rows.",
    down: "Smaller profiles; the metric values do not change.",
  },
  eval_row_flags_top_k: {
    purpose: "Planned: rows kept per privacy check in evaluation_row_flags.",
    up: "More flagged rows to inspect.",
    down: "Fewer rows stored.",
  },
  eval_row_flags_source_keys: {
    purpose: "Planned: hashed stores a salted hash of a matched source key; raw stores the key itself.",
    up: "raw exposes source keys in a results table.",
    down: "hashed (default) lines keys up without revealing them.",
  },
  eval_max_bytes_billed: {
    purpose: "Planned: maximumBytesBilled on every evaluator query.",
    up: "Bigger tables can be evaluated; a mistake costs more.",
    down: "Queries over the cap are refused before they run.",
  },
  eval_max_shuffle_gb: {
    purpose: "Planned: the predicted Beam shuffle above which the evaluator refuses to launch.",
    up: "Bigger evaluations run.",
    down: "More launches refused.",
  },
  eval_scope: {
    purpose:
      "Planned: which synthetic rows count — the whole table, a snapshot, the rows the run appended, or a manual window.",
    up: "Wider scopes mix runs.",
    down: "Narrower scopes isolate one run's rows.",
  },
  eval_allow_contaminated: {
    purpose: "Planned: evaluate a table whose scope check found rows from other runs instead of refusing it.",
    up: "true evaluates anyway (the registry still records contaminated).",
    down: "false (default) refuses a contaminated scope.",
  },
};

function knobConcept(k: (typeof knobsFile.knobs)[number]): Concept {
  const guide = knobGuides[k.id as KnobId];
  const links: ConceptLink[] = [];
  if (k.source !== "planned") {
    const match = /^(.*?):(\d+)$/.exec(k.source);
    links.push(code(`Code — ${k.source}`, match?.[1] ?? k.source, match?.[2] ? Number(match[2]) : undefined));
  }
  for (const number of k.related_adrs) {
    const link = adrLink(number);
    if (link) links.push(link);
  }
  for (const path of k.docs) links.push(doc(path.split("/").pop() ?? path, path));
  links.push(...(guide.refs ?? []));
  return {
    id: `knob:${k.id}`,
    title: k.label,
    purpose: guide.purpose,
    ...(guide.formula ? { formula: guide.formula } : {}),
    ...(guide.tip ? { interpretation: { tip: guide.tip } } : {}),
    links,
  };
}

// ---------------------------------------------------------- stats: concepts --

const statsConcepts: Concept[] = [
  {
    id: "stats:dkw",
    title: "DKW band ε(n)",
    purpose:
      "With probability 1 − α, the empirical CDF of n rows sits within ε of the true CDF everywhere. It bounds every decile, null rate and category share the sample tier reports.",
    formula:
      "P\\left(\\sup_x |\\hat F_n(x) - F(x)| > \\varepsilon\\right) \\le 2e^{-2n\\varepsilon^2} \\;\\Rightarrow\\; \\varepsilon = \\sqrt{\\frac{\\ln(2/\\alpha)}{2n}}",
    interpretation: {
      good: `n = ${fmt(N_DEFAULT)} pins each column's marginal to ±${EPS_DEFAULT} of mass at 95 % confidence, whatever the source size.`,
      bad: "Halving the band costs four times the rows, forever.",
      tip: "The 95 % holds per column: across many columns at once, some column misses its band more often. Thresholds tighter than about 2ε test sampling noise, not the generator.",
    },
    diagram: "config:dkw-band",
    links: [P.dkw, P.massart, doc("Reference-sample scaling §3.1", SCALING)],
  },
  {
    id: "stats:amplification",
    title: "Amplification M/n and M/N",
    purpose:
      "How many generated rows each reference row, and each source row, stands behind. It does not change the sampling error; it multiplies the cost of every estimation error.",
    formula: "A_{\\text{sample}} = \\frac{M}{n},\\qquad A_{\\text{source}} = \\frac{M}{N}",
    interpretation: {
      tip: "N never enters the error formula either: a 10k sample of 1M rows and of 1B rows have the same DKW band.",
      bad: "At high amplification a mis-estimated tail or a missing category becomes a property of every generated row.",
    },
    links: [doc("Reference-sample scaling §1 (n follows the estimand)", SCALING)],
  },
  {
    id: "stats:rare-capture",
    title: "Rare-category capture",
    purpose:
      "The probability that a category with share p appears at least once in n rows. A category absent from the sample is absent from all generated output.",
    formula:
      "P(\\text{seen}) = 1 - (1 - p)^n,\\qquad n_{95\\%} \\approx 3/p,\\qquad \\frac{\\mathrm{SE}(\\hat p)}{p} \\approx \\frac{1}{\\sqrt{np}} = 10\\% \\iff n \\approx 100/p",
    interpretation: {
      good: "At n = 10k a 1 % category is well estimated.",
      bad: "A 0.01 % category is a coin flip to exist in the sample.",
      tip: "100/p buys a 10 % relative standard error — about 68 % confidence, not 95 %. Good–Turing: the unseen mass is about singletons/n, a per-column diagnostic.",
    },
    diagram: "config:rare-capture",
    links: [P.good, doc("Reference-sample scaling §3.2", SCALING)],
  },
  {
    id: "stats:tail-points",
    title: "Tail support n(1 − q)",
    purpose:
      "The expected number of sample points beyond the q-quantile. A tail estimate is only as good as the points behind it.",
    formula: "t = n(1 - q),\\qquad \\text{want } t \\ge 20",
    interpretation: {
      good: "p99 at n = 10k rests on 100 points.",
      bad: "p99.9 rests on 10 points, p99.99 on one.",
      tip: "For tails that matter: a per-table bump, a parametric tail, or full-table profiling.",
    },
    diagram: "config:tail",
    links: [doc("Reference-sample scaling §3.3", SCALING)],
  },
  {
    id: "stats:pool-reuse",
    title: "Pool reuse",
    purpose: `A free-text pool holds min(M, D, ${fmt(POOL_CAP)}) values (the code's pool target), so M generated rows reuse each value M divided by that many times. It bounds a column's diversity, not its fidelity.`,
    formula: `r = \\frac{M}{\\min(M,\\ D,\\ ${TEX_POOL_CAP})}`,
    interpretation: {
      bad: `At 90M rows and a full pool each value repeats about ${fmt(Math.round(90_000_000 / POOL_CAP))} times: a pool column can never carry identifiers.`,
      tip: "Shape expansion (--freetext_expansion) and keyspace generation carry high-cardinality columns.",
    },
    links: [adr("0018"), adr("0020"), doc("Reference-sample scaling §6.1", SCALING)],
  },
  {
    id: "stats:birthday",
    title: "Identifier collisions (birthday bound)",
    purpose: "The chance that M identifiers drawn uniformly from a keyspace of K values contain a repeat.",
    formula: "P \\approx 1 - e^{-M(M-1)/2K},\\qquad E[\\text{pairs}] = \\frac{M(M-1)}{2K}",
    interpretation: {
      bad: "90M ids from a 12-digit space contain about 4,050 colliding pairs.",
      tip: "Fewer than one expected pair needs K > M²/2: at 50M rows about 1.25·10¹⁵ (≈ 2⁵⁰).",
    },
    diagram: "config:birthday",
    links: [doc("Reference-sample scaling §6.1", SCALING)],
  },
  {
    id: "stats:distinct-truncation",
    title: "Sample-tier distinct truncation",
    purpose:
      "A sample of n rows can never report more than n distinct values, and a sparse column far fewer; no inequality bounds the gap. The sample tier's statistics keep that truncated count, and only the exact tier (the whole table) records the true one.",
    formula:
      "\\hat D_{\\text{sample}} \\le \\min(D,\\ n\\,s),\\qquad E[\\hat D] \\le D\\left(1 - (1 - 1/D)^{ns}\\right)\\ \\text{(uniform model: an upper bound)}",
    interpretation: {
      bad: "The R6 pair sized a pool from the sample: 94 distinct seen, 4,022 in the source (ADR 0033).",
      tip: `Pools no longer starve on it: _pool_target takes exact stats, else the source filter's cardinality, else the sample distinct (ADR 0033 D2). The sample sizes a pool only with no source-value store attached, or above the store's ${fmt(SOURCE_CAP)}-value cap.`,
    },
    links: [
      adr("0022"),
      {
        label: "ADR 0033 D2 — the pool target's distinct count",
        url: blob("docs/adr/0033-pool-ladder-integrity-at-scale.md", 58),
        kind: "adr",
      },
      code("_pool_target (b1 engine)", "packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py", 1569),
      P.hurlbert,
      doc("Source-table stats — why two tiers", STATS_DOC),
    ],
  },
  {
    id: "stats:hll",
    title: "HyperLogLog++ distinct counts",
    purpose:
      "APPROX_COUNT_DISTINCT hashes each value to 64 bits and keeps, in 2^p registers, the longest run of leading zeros; a bias-corrected harmonic mean turns them into a distinct count.",
    formula: "\\hat D = \\alpha_m\\, m^2 \\left(\\sum_{j=1}^{m} 2^{-R_j}\\right)^{-1}",
    interpretation: {
      good: "About 0.5 % typical error at a fixed small memory, mergeable across shards: one scan does every column.",
      tip: "The exact tier's only job the sample cannot do: true cardinality.",
    },
    diagram: "config:hll",
    links: [P.hll, P.bqHll, adr("0022")],
  },
  {
    id: "stats:entropy",
    title: "Shannon entropy H",
    purpose:
      "The average information per value, in bits. Two columns with the same distinct count can have very different entropy: balanced versus collapsed.",
    formula: "H(X) = -\\sum_i p_i \\log_2 p_i",
    interpretation: {
      tip: "The profiler computes it from the same counter as distinct, so it costs O(distinct).",
      bad: "A synthetic column whose entropy sits far below the source has collapsed, even when distinct looks healthy.",
    },
    diagram: "config:entropy",
    links: [P.shannon, doc("Source-table stats §1", STATS_DOC)],
  },
  {
    id: "stats:entropy-norm",
    title: "Normalized entropy",
    purpose:
      "Entropy divided by its maximum log₂(distinct): 1 means uniform over the observed support, near 0 means one value dominates.",
    formula: "H_{\\text{norm}} = \\frac{H}{\\log_2 D} \\in [0, 1]",
    interpretation: {
      tip: "Cardinality-independent, so it compares across columns; read it with top1_share.",
    },
    links: [P.shannon, doc("Source-table stats §1", STATS_DOC)],
  },
  {
    id: "stats:deciles",
    title: "Deciles and inverse-CDF sampling",
    purpose:
      "The profiler stores 11 quantile points p0…p100; drawing u ~ U(0, 1) and reading x = F⁻¹(u) reproduces a skewed marginal that uniform-in-range would flatten.",
    formula: "X = F^{-1}(U),\\quad U \\sim \\mathrm{Uniform}(0, 1)",
    interpretation: {
      tip: "On the sample tier each decile's mass is within ±ε(n_v) of the truth, where n_v = n·(1 − null − empty): the deciles are computed over non-null, non-empty values only, so a sparse column has a wider band than its null rate.",
      bad: "Between two knots the draw is linear: 11 points cannot reproduce a shape inside a decile.",
    },
    links: [P.devroye, adr("0022"), adr("0025"), doc("Source-table stats §2", STATS_DOC)],
  },
  {
    id: "stats:null-patterns",
    title: "Null patterns",
    purpose:
      "Which columns are null together in a row, as the top bitstrings of the __table__ entry. Per-column rates are its marginals; sampling them independently invents patterns the source never had.",
    formula: "P(\\text{null}_A, \\text{null}_B) \\ne P(\\text{null}_A)\\,P(\\text{null}_B)",
    interpretation: {
      tip: "Measured today, not yet consumed: the designed consumer samples row patterns instead of per-column coin flips.",
    },
    diagram: "config:null-patterns",
    links: [doc("Source-table stats §4", STATS_DOC)],
  },
  {
    id: "stats:null-rate",
    title: "Null and empty rates (Wilson interval)",
    purpose:
      "The share of rows that are NULL, or a trimmed-empty string, with a 95 % Wilson score interval on the sample tier.",
    formula:
      "\\frac{\\hat p + \\frac{z^2}{2n} \\pm z\\sqrt{\\frac{\\hat p(1-\\hat p)}{n} + \\frac{z^2}{4n^2}}}{1 + z^2/n}",
    interpretation: { tip: "The exact tier overwrites the fractions with full-table values: no interval to show." },
    links: [P.wilson],
  },
  {
    id: "stats:shape-mix",
    title: "Shape mix",
    purpose:
      "The share of each character-shape mask (9 digit, A upper, a lower) in a string column: the format, not the value.",
    interpretation: { tip: "Identifier-like columns expand from their shape mix instead of an LLM pool." },
    links: [doc("Free-text expansion modes", "docs/designs/2026-08-05-freetext-expansion-modes.md")],
  },
  {
    id: "stats:temporal-mix",
    title: "Temporal mixes",
    purpose: "Day-of-week, hour and month shares of a temporal column, plus its range and future share.",
    interpretation: {
      tip: "A global decile vector does not preserve weekday or hour structure; these mixes are measured for a bucket-preserving sampler.",
    },
    links: [doc("Source-table stats §3", STATS_DOC)],
  },
  {
    id: "stats:duplicate-share",
    title: "Random-draw duplicate share",
    purpose:
      "The share of M uniform draws from a capacity K that repeat an earlier draw — the PK-capacity check for keys drawn at random.",
    formula: "1 - \\frac{K}{M}\\left(1 - e^{-M/K}\\right)",
    interpretation: {
      tip: `At K = ${MARGIN}·M it is ${DUP_AT_MARGIN}; driven children draw without replacement and never collide (ADR 0036).`,
    },
    links: [adr("0035"), adr("0036")],
  },
];

// ------------------------------------------------------- config: concepts --

const configConcepts: Concept[] = [
  {
    id: "config:amp",
    title: "The pipeline amp",
    purpose:
      "Every knob the pipeline reads, as a console: dials and switches you can turn in a what-if, fixed screws for constants. Values come from the code (knobs.json), never from the docs.",
    interpretation: {
      tip: "Focus a dial: arrows turn it, Page Up/Down jump, Home/End go to the ends, Enter opens its sheet.",
    },
    diagram: "config:screw",
    links: [code("scripts/gui/export_knobs.py", "scripts/gui/export_knobs.py")],
  },
  {
    id: "config:settable-via",
    title: "Settable via",
    purpose:
      "How a knob can be changed: a launcher CLI flag, a Composer DAG param, a Flex Template param, or not at all (a code constant or a derived value).",
    interpretation: { tip: "A Composer default can differ from the CLI default; the sheet shows both." },
    links: [code("docker/flex_template_metadata.json", "docker/flex_template_metadata.json")],
  },
  {
    id: "config:planned",
    title: "Planned knob",
    purpose:
      "An evaluator flag documented by the evaluation plan whose CLI does not exist at the exported commit. Its default is the plan's, and the exporter will read it from code once it ships.",
    links: [doc("Evaluation framework design", "docs/designs/2026-07-07-evaluation-framework-design.md")],
  },
  {
    id: "config:stats-tier",
    title: "Stats tier (sample vs exact)",
    purpose:
      "sample profiles the reference sample (fractions and deciles DKW-bounded, distinct capped at n); exact merges one full-table aggregate scan (HLL++ distinct, deciles, top-k).",
    interpretation: {
      tip: "A NULL stats_tier (rows written before the exact tier existed) is the sample tier.",
    },
    links: [adr("0022"), doc("Source-table stats — the tiers", STATS_DOC)],
  },
  {
    id: "config:snapshot",
    title: "Stats snapshot",
    purpose:
      "One profiler pass: all rows sharing (reference digest, tier, profiler version, run). One digest can carry a sample and an exact snapshot, and several profiler versions.",
    links: [adr("0022")],
  },
  {
    id: "config:time-estimate",
    title: "Time estimate",
    purpose:
      "A linear extrapolation of a measured run's phases to M rows: startup and the cold pool branch stay fixed, generation and dedup plus load scale with the rows.",
    formula:
      "T(M) = T_{\\text{start}} + T_{\\text{pools}} + \\left(T_{\\text{gen}} + T_{\\text{dedup}}\\right)\\frac{M}{2\\times10^7}",
    interpretation: {
      bad: "The measured runs left initial_workers empty, so their fleets ramped to full size mid-run; the linear estimate inherits that ramp and overestimates a fleet pinned full. Table width and the load path change it too.",
      tip: "The measured numbers are typed once, in the figure script's MEASURED block (knobs.json measured).",
    },
    links: [adr("0034"), code("scripts/doc/make_throughput_figures.py", "scripts/doc/make_throughput_figures.py")],
  },
  {
    id: "config:warm-cold",
    title: "Warm vs cold runs",
    purpose:
      "A cold run builds its free-text pools and RAG chunks; a warm run finds them persisted for the same reference digest and model and skips the LLM.",
    interpretation: {
      tip: "The pool branch is the only phase warming removes; the scenario shows the measured cold and warm walls.",
    },
    links: [adr("0020"), adr("0034")],
  },
  {
    id: "config:pool-economics",
    title: "Pool persistence economics",
    purpose:
      "Pools built inside every worker's setup repeat the same LLM work per process; persisting them once per digest removes it from every later run.",
    interpretation: {
      tip: "The scenario shows the LLM service time the measured 1M-row run spent on its pool builds.",
    },
    links: [adr("0020"), doc("WS5 generation throughput", "docs/designs/2026-07-26-ws5-generation-throughput.md", 40)],
  },
  {
    id: "config:three-lines",
    title: "Mode A: lines of defence",
    purpose:
      "Every generated row crosses in-pipeline gates before it lands: per-record Pydantic, per-batch Pandera, then uniqueness (and FK integrity where FKs are enforced). Every reject goes to the DLQ with its context.",
    diagram: "config:three-lines",
    links: [
      P.pydantic,
      P.pandera,
      P.beamDlq,
      code("sdfb_beam/pipeline.py", "packages/sdfb-beam/src/sdfb_beam/pipeline.py", 362),
    ],
  },
  {
    id: "config:dlq",
    title: "Dead-letter queue (DLQ)",
    purpose:
      "Rows a gate rejects are never dropped: they land in the DLQ table with rule_id, error_type, pipeline_step, stage and the error detail.",
    interpretation: { tip: "A rule can be declared, emitted and counted independently; the table shows all three." },
    links: [P.beamDlq, code("sdfb_core/validation/dlq.py", "packages/sdfb-core/src/sdfb_core/validation/dlq.py")],
  },
  {
    id: "config:fk-rules",
    title: "fk.orphan vs fk.unmatched",
    purpose:
      "fk.orphan: a generated child row references a parent key that was not landed, caught before write. fk.unmatched: a parent key had no candidate on a conditional edge, caught before generation.",
    links: [adr("0031"), adr("0037")],
  },
  {
    id: "config:blocker-gate",
    title: "BLOCKER gate",
    purpose:
      "After the load jobs commit, the run fails when the share of rows failing a counted BLOCKER rule exceeds the env's ratio.",
    formula:
      "\\frac{\\sum_{r \\in \\text{counted}} \\text{dlq}_r}{\\text{valid} + \\sum_{r \\notin \\text{excluded}} \\text{dlq}_r} > r_{\\text{env}} \\Rightarrow \\text{FAILED\\_BLOCKER}",
    interpretation: {
      tip: "An ADR 0038 adjusted table excludes pk.duplicate from both sides of the ratio.",
    },
    links: [
      code("sdfb_core/validation/summary.py", "packages/sdfb-core/src/sdfb_core/validation/summary.py", 182),
      adr("0038"),
    ],
  },
  {
    id: "config:uniqueness-modes",
    title: "Uniqueness modes",
    purpose:
      "exact removes duplicates behind one full-row shuffle; exact_chained uses the older three-barrier chain; streaming lands rows unshuffled and only measures duplicates.",
    diagram: "config:uniqueness",
    links: [adr("0034"), adr("0036")],
  },
];

export const concepts = defineConcepts([...statsConcepts, ...configConcepts, ...knobsFile.knobs.map(knobConcept)]);
