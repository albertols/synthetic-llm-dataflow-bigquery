/**
 * CONFIG tab concepts (namespaces `knob:`, `config:`, `stats:`) and the knob
 * guides behind every knob sheet.
 *
 * - `knobGuides`: one entry per knobs.json knob (typed by `KnobId`, so a new
 *   knob fails the typecheck until it has a guide): purpose, what turning it
 *   up or down does, and a practical tip. The generator knobs' guides come
 *   from the setting catalogue (generated/knobGuides.ts, ADR 0043); the
 *   evaluator's are written here. Values, defaults and sources are never
 *   typed here — they come from generated/knobs.json.
 * - `knob:<id>` concepts are derived from the guides plus the knob's code,
 *   ADR and doc links (at the commit the export ran on).
 * - `stats:` and `config:` concepts explain the maths and the guardrails,
 *   each with its primary sources.
 */
import {
  generatorGuides,
  type GeneratedGuide,
  type GeneratedGuideRef,
  type GeneratorKnobId,
} from "../../generated/knobGuides";
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
  "0027": { file: "0027-verified-wave4-operational-integrity.md", title: "Wave 4 verified; operational integrity" },
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
  "0041": {
    file: "0041-evaluation-standalone-package.md",
    title: "Evaluation is a standalone package and a separate job",
  },
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
  bqHll: {
    label: "BigQuery HLL++ functions",
    url: "https://cloud.google.com/bigquery/docs/reference/standard-sql/hll_functions",
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

/** A catalogue ref as a concept link; repo docs and code resolve at CONFIG_LINK_REF like every other link here. */
function toLink(ref: GeneratedGuideRef): ConceptLink {
  if (ref.kind === "paper") return paper(ref.label, ref.url!);
  if (ref.path === undefined) return { label: ref.label, url: ref.url!, kind: "docs" };
  return ref.kind === "doc" ? doc(ref.label, ref.path, ref.line) : code(ref.label, ref.path, ref.line);
}

/** The generator knobs' guides: the setting catalogue's prose (scripts/gui/export_knobs.py → knob_guides.json). */
const generatorKnobGuides = Object.fromEntries(
  (Object.entries(generatorGuides) as [GeneratorKnobId, GeneratedGuide][]).map(([id, { refs, ...guide }]) => [
    id,
    refs ? { ...guide, refs: refs.map(toLink) } : guide,
  ]),
) as Record<GeneratorKnobId, KnobGuide>;

export const knobGuides: Readonly<Record<KnobId, KnobGuide>> = {
  ...generatorKnobGuides,
  // -------------------------------------------------------------- EVALUATION
  eval_mode: {
    purpose:
      "exact reads every row of both sides; sampled reads a salted row sample of each table larger than --sample_rows. Unset, the runner decides: sampled on the DirectRunner, exact on Dataflow.",
    up: "exact: no sampling error, every metric evaluated, full-table cost.",
    down: "sampled: cheaper, with floors and intervals as wide as the sample; metrics that need every row of a side are not evaluated.",
  },
  eval_sample_rows: {
    purpose: "Sampled mode: the rows read per side of a table larger than this. A smaller table is read in full.",
    up: "Tighter noise floors (1/√n), more bytes scanned.",
    down: "Cheaper, wider floors.",
    formula: "\\varepsilon(2\\times10^5) \\approx 0.0030",
  },
  eval_privacy_sample_rows: {
    purpose: "Rows of the nearest-neighbour privacy sample: the distance-to-closest-record and NNDR checks.",
    up: "Rarer copies become detectable; nearest-neighbour cost grows quadratically.",
    down: "Faster, less sensitive privacy checks.",
  },
  eval_detection_sample_rows: {
    purpose: "Rows per side in the detection sample: the classifier two-sample test.",
    up: "A tighter AUC interval.",
    down: "A wider AUC interval.",
  },
  eval_pair_max_columns: {
    purpose: "Columns whose pairs are compared, per table: the correlation and contingency metrics.",
    up: "Quadratically more pairs.",
    down: "Joint structure checked on fewer columns.",
  },
  eval_row_flags_top_k: {
    purpose: "Row flags kept per check and table in evaluation_row_flags.",
    up: "More flagged rows to inspect.",
    down: "Fewer rows stored.",
  },
  eval_row_flags_source_keys: {
    purpose:
      "How a row flag names the matched source record: as a keyed hash only. hashed is the one value; raw source keys are never written.",
    up: "No other value: the flag exists so a launch states the policy.",
    down: "hashed lines a source record up across the checks of one evaluation without revealing its key.",
  },
  eval_max_bytes_billed: {
    purpose:
      "BigQuery bytes the evaluation may process. The planner adds up its dry runs and refuses the evaluation before anything is billed.",
    up: "Bigger tables can be evaluated; a mistake costs more.",
    down: "An evaluation over the cap is refused at planning.",
  },
  eval_max_shuffle_gb: {
    purpose:
      "Beam shuffle the value census may use. Above it, high-cardinality columns are value-sampled instead of counted exactly; nothing is refused.",
    up: "More columns keep an exact census.",
    down: "More columns are value-sampled (their rows say method = value_sampled).",
  },
  eval_scope: {
    purpose:
      "How the job's landing rows are isolated: auto picks from the write disposition and the job's commit window (table, as_of, appends, as_of_diff); manual reads the table as it is now.",
    up: "manual: whatever the table holds now, other runs' rows included.",
    down: "auto: only the rows this job wrote, checked against its committed row count.",
  },
  eval_allow_contaminated: {
    purpose: "Evaluate a scope another writer touched instead of refusing it.",
    up: "true evaluates anyway; the scope stays marked contaminated.",
    down: "false (default) refuses a contaminated scope.",
  },
};

function knobConcept(k: (typeof knobsFile.knobs)[number]): Concept {
  const guide = knobGuides[k.id as KnobId];
  const links: ConceptLink[] = [];
  const match = /^(.*?):(\d+)$/.exec(k.source);
  links.push(code(`Code — ${k.source}`, match?.[1] ?? k.source, match?.[2] ? Number(match[2]) : undefined));
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
      "How a knob can be changed: a launcher CLI flag, a Composer DAG param, a Flex Template param, a key in a config file (--config_uri), or not at all (a code constant or a derived value).",
    interpretation: { tip: "A Composer default can differ from the CLI default; the sheet shows both." },
    links: [code("docker/flex_template_metadata.json", "docker/flex_template_metadata.json")],
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
      code("sdfb_beam/pipeline.py", "packages/sdfb-beam/src/sdfb_beam/pipeline.py", 376),
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
