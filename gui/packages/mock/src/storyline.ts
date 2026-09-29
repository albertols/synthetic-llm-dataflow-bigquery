/**
 * The mock's storyline: 40 evaluations over the 8 weeks 2026-08-03 … 2026-09-27.
 *
 *  weeks 1–2  b1_rag on the sample tier; the 512-value pool collapses on
 *             users.last_name (freetext_expansion off) and e-mails leak from
 *             the reference sample (memorization fails);
 *  weeks 3–4  kcenter seeding and source-domain rejection shrink the leak; b2
 *             enters for comparison; one evaluation FAILED, one count_mismatch;
 *  weeks 5–6  the exact tier and identifier expansion fix the collapse; the leak
 *             is gone; one SKIPPED (empty appends scope), one PARTIAL with an
 *             unverified reference (privacy not_evaluated);
 *  weeks 7–8  10M and 90M runs, the 200-column table, prd; the last one is still RUNNING.
 *
 * A spec only states parameters and generator behaviour (`quality`): every
 * metric, score and status is computed from the generated data.
 */

export type Engine = "b1_rag" | "b2_library";
export type Llm = "gemma4-e4b" | "gemma4-26b-a4b-awq" | "qwen2.5-7b";
export type EmbedderId = "hashing-384" | "bge-small-en-v1.5";
export type Retrieval = "centroid" | "kcenter" | "kcenter_rotate";

export interface Quality {
  /** Share of free-text pool values copied verbatim from the reference sample. */
  valueLeak: number;
  /** Share of synthetic rows whose non-key columns copy a reference row. */
  rowLeak: number;
  /** Share of rows echoing one of the 1,024 prompt-exposed rows. */
  exposureLeak: number;
  /** Share of rows equal to a reference row but for one field. */
  nearLeak: number;
  /** Free-text pool size cap reached with no expansion (distinct = pool). */
  poolCollapse: boolean;
  /** Share of joint structure a b2 row keeps (0 = independent marginals, as b1). */
  corrKeep: number;
  /** Extra corruption of synthetic marginals (0 = none). */
  drift: number;
  /** Share of order_items.product_id keys with no landed parent. */
  orphanShare: number;
  /** Share of rows with a type-invalid value in the first free-text column. */
  invalidShare: number;
  /** b1's interim temporal sampler: a similarity blend with uniform (weeks 1–4); later runs follow the source. */
  temporalBlend: boolean;
}

export interface EvalSpec {
  index: number;
  id: string;
  evaluatedAt: string;
  durationMinutes: number;
  trigger: "cli" | "composer" | "chained" | "agent";
  runner: "DataflowRunner" | "DirectRunner";
  mode: "exact" | "sampled";
  engine: Engine;
  llm: Llm;
  embedder: EmbedderId;
  retrieval: Retrieval;
  seed: string;
  similarity: number;
  referenceRowsLimit: number;
  numRows: number;
  sourceStatsTier: "sample" | "exact";
  env: "dev" | "uat" | "prd";
  uniquenessMode: "exact" | "exact_chained" | "streaming";
  freetextExpansion: "off" | "identifiers" | "all";
  vllmDtype: "float16" | "bfloat16";
  tables: string[];
  relational: boolean;
  quality: Quality;
  /** Forced outcomes (never inferred from metrics). */
  outcome?: "FAILED" | "SKIPPED" | "RUNNING";
  statusReason?: string;
  scopeStatus?: "count_mismatch" | "empty";
  /** A table whose scope check flagged something the evaluation still measured (a warning, not PARTIAL). */
  scopeNote?: { table: string; status: "contaminated" | "expired"; reason: string };
  referenceVerified?: boolean;
  warnings?: string[];
  /** Sampled mode only: the evaluation's --sample_rows (default: the eval_sample_rows knob). */
  sampleRows?: number;
  /**
   * Producer inputs staged on one evaluation each, so the mock has a real row for every scorer
   * state the EVALUATION tab explains; the scorer (scoreRow) still decides every status.
   */
  staged?: Staged;
}

export interface Staged {
  /** This metric's rows arrive without their noise input (floor or CI): WARN/FAIL stays, "noise check unavailable" (R41). */
  withoutNoiseInput?: string;
  /** A numeric column whose source spread is 0 in this scope while the synthetic side varies: std ratio +∞, FAIL (R43). */
  spreadFromConstant?: { table: string; column: string };
}

const THELOOK = ["users", "orders", "order_items"];
const CLEAN: Quality = {
  valueLeak: 0,
  rowLeak: 0,
  exposureLeak: 0,
  nearLeak: 0,
  poolCollapse: false,
  corrKeep: 0,
  drift: 0,
  orphanShare: 0,
  invalidShare: 0,
  temporalBlend: false,
};

type Row = Partial<Omit<EvalSpec, "index" | "id" | "evaluatedAt" | "quality">> & {
  day: string;
  at: string;
  quality?: Partial<Quality>;
};

/** One line per evaluation: date, time and what differs from the defaults. */
const ROWS: Row[] = [
  // Week 1 — b1 on the sample tier: collapse + leak.
  {
    day: "2026-08-03",
    at: "07:12",
    quality: {
      valueLeak: 0.34,
      rowLeak: 0.012,
      exposureLeak: 0.02,
      nearLeak: 0.01,
      poolCollapse: true,
      invalidShare: 0.0004,
    },
  },
  {
    day: "2026-08-04",
    at: "09:40",
    embedder: "bge-small-en-v1.5",
    quality: { valueLeak: 0.31, rowLeak: 0.011, exposureLeak: 0.018, nearLeak: 0.01, poolCollapse: true },
  },
  {
    // A DirectRunner smoke evaluation at --sample_rows 60: noise floors this wide explain several
    // WARN and FAIL crossings away ("≈ within noise, was FAIL", Ruling R40).
    day: "2026-08-05",
    at: "14:05",
    runner: "DirectRunner",
    mode: "sampled",
    trigger: "cli",
    sampleRows: 60,
    numRows: 1_000_000,
    quality: { valueLeak: 0.3, rowLeak: 0.01, exposureLeak: 0.015, nearLeak: 0.008, poolCollapse: true },
  },
  {
    day: "2026-08-06",
    at: "08:30",
    similarity: 0.8,
    quality: { valueLeak: 0.41, rowLeak: 0.016, exposureLeak: 0.025, nearLeak: 0.012, poolCollapse: true },
  },
  {
    day: "2026-08-07",
    at: "16:20",
    seed: "42",
    quality: { valueLeak: 0.28, rowLeak: 0.009, exposureLeak: 0.015, nearLeak: 0.008, poolCollapse: true },
  },
  // Week 2 — still collapsing; an orphan regression on the external edge.
  {
    day: "2026-08-10",
    at: "07:55",
    embedder: "bge-small-en-v1.5",
    staged: { withoutNoiseInput: "row.exact_match_rate_nonkey" },
    quality: {
      valueLeak: 0.26,
      rowLeak: 0.008,
      exposureLeak: 0.012,
      nearLeak: 0.007,
      poolCollapse: true,
      orphanShare: 0.004,
    },
  },
  {
    day: "2026-08-11",
    scopeNote: {
      table: "users",
      status: "expired",
      reason:
        "the source snapshot is older than the 7-day time-travel window; fidelity measured against the current table",
    },
    at: "10:10",
    similarity: 0.3,
    quality: { valueLeak: 0.22, rowLeak: 0.007, exposureLeak: 0.01, nearLeak: 0.006, poolCollapse: true, drift: 0.05 },
  },
  {
    day: "2026-08-12",
    at: "13:45",
    referenceRowsLimit: 50_000,
    quality: { valueLeak: 0.2, rowLeak: 0.006, exposureLeak: 0.01, nearLeak: 0.006, poolCollapse: true },
  },
  {
    day: "2026-08-13",
    at: "09:00",
    numRows: 10_000_000,
    staged: { spreadFromConstant: { table: "users", column: "age" } },
    quality: { valueLeak: 0.19, rowLeak: 0.006, exposureLeak: 0.009, nearLeak: 0.005, poolCollapse: true },
  },
  {
    day: "2026-08-14",
    at: "15:30",
    trigger: "chained",
    quality: { valueLeak: 0.18, rowLeak: 0.005, exposureLeak: 0.008, nearLeak: 0.005, poolCollapse: true },
  },
  // Week 3 — kcenter seeding, source-domain rejection; b2 for comparison.
  {
    day: "2026-08-17",
    at: "08:15",
    retrieval: "kcenter",
    quality: { valueLeak: 0.09, rowLeak: 0.003, exposureLeak: 0.004, nearLeak: 0.003, poolCollapse: true },
  },
  {
    day: "2026-08-18",
    at: "11:20",
    engine: "b2_library",
    llm: "qwen2.5-7b",
    vllmDtype: "float16",
    quality: { valueLeak: 0.05, rowLeak: 0.002, exposureLeak: 0.002, nearLeak: 0.002, corrKeep: 0.7 },
  },
  {
    day: "2026-08-19",
    at: "09:35",
    retrieval: "kcenter",
    embedder: "bge-small-en-v1.5",
    quality: { valueLeak: 0.07, rowLeak: 0.002, exposureLeak: 0.003, nearLeak: 0.002, poolCollapse: true },
  },
  {
    day: "2026-08-20",
    at: "14:50",
    outcome: "FAILED",
    statusReason:
      "BigQuery rejected the source profile query: bytes billed would exceed max_bytes_billed (1 TiB); no metrics were written.",
  },
  {
    day: "2026-08-21",
    at: "10:05",
    engine: "b2_library",
    llm: "qwen2.5-7b",
    vllmDtype: "float16",
    similarity: 0.3,
    quality: { valueLeak: 0.04, rowLeak: 0.001, exposureLeak: 0.001, nearLeak: 0.001, corrKeep: 0.65, drift: 0.04 },
  },
  // Week 4 — count mismatch; the leak keeps shrinking.
  {
    day: "2026-08-24",
    at: "08:40",
    retrieval: "kcenter",
    quality: { valueLeak: 0.05, rowLeak: 0.0015, exposureLeak: 0.002, nearLeak: 0.001, poolCollapse: true },
  },
  {
    day: "2026-08-25",
    at: "12:25",
    retrieval: "kcenter",
    scopeStatus: "count_mismatch",
    statusReason:
      "orders: 1,212,044 synthetic rows in scope, 1,250,000 expected (a partial re-run appended to the landing table).",
    quality: { valueLeak: 0.04, rowLeak: 0.001, exposureLeak: 0.001, nearLeak: 0.001, poolCollapse: true },
  },
  {
    day: "2026-08-26",
    at: "09:15",
    engine: "b2_library",
    llm: "gemma4-26b-a4b-awq",
    quality: { valueLeak: 0.03, rowLeak: 0.001, exposureLeak: 0.001, nearLeak: 0.001, corrKeep: 0.72 },
  },
  {
    day: "2026-08-27",
    at: "16:00",
    retrieval: "kcenter_rotate",
    seed: "7",
    quality: { valueLeak: 0.03, rowLeak: 0.0008, exposureLeak: 0.0008, nearLeak: 0.0008, poolCollapse: true },
  },
  {
    day: "2026-08-28",
    at: "11:10",
    retrieval: "kcenter",
    referenceRowsLimit: 50_000,
    quality: { valueLeak: 0.025, rowLeak: 0.0006, exposureLeak: 0.0006, nearLeak: 0.0006, poolCollapse: true },
  },
  // Week 5 — the exact tier and identifier expansion: collapse fixed, leak gone.
  {
    day: "2026-08-31",
    at: "08:05",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter",
    quality: {},
  },
  {
    day: "2026-09-01",
    at: "10:45",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter",
    embedder: "bge-small-en-v1.5",
    quality: {},
  },
  {
    day: "2026-09-02",
    at: "13:30",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter_rotate",
    llm: "gemma4-26b-a4b-awq",
    quality: {},
  },
  {
    day: "2026-09-03",
    at: "07:50",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    outcome: "SKIPPED",
    scopeStatus: "empty",
    trigger: "composer",
    statusReason:
      "appends scope: no synthetic rows landed after the previous evaluation's window_end; nothing to evaluate.",
  },
  {
    day: "2026-09-04",
    at: "15:20",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    engine: "b2_library",
    llm: "qwen2.5-7b",
    vllmDtype: "float16",
    similarity: 0.8,
    quality: { valueLeak: 0.012, rowLeak: 0.0004, corrKeep: 0.75 },
  },
  // Week 6 — unverified reference; 10M runs.
  {
    day: "2026-09-07",
    at: "09:05",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter",
    numRows: 10_000_000,
    quality: {},
  },
  {
    day: "2026-09-08",
    at: "11:40",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter",
    referenceVerified: false,
    statusReason:
      "reference digest did not match the pinned source snapshot (the source table was rewritten after the launch); privacy lifts not evaluated.",
    quality: {},
  },
  {
    day: "2026-09-09",
    at: "14:15",
    sourceStatsTier: "exact",
    freetextExpansion: "all",
    retrieval: "kcenter_rotate",
    seed: "42",
    quality: {},
  },
  {
    day: "2026-09-10",
    at: "08:25",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    engine: "b2_library",
    llm: "gemma4-26b-a4b-awq",
    numRows: 10_000_000,
    quality: { corrKeep: 0.78 },
  },
  {
    day: "2026-09-11",
    scopeNote: {
      table: "order_items",
      status: "contaminated",
      reason: "rows from another run_id share the landing table; the scope kept only this run's rows",
    },
    at: "12:55",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter",
    env: "uat",
    referenceRowsLimit: 50_000,
    quality: {},
  },
  // Week 7 — scale and the wide table.
  {
    day: "2026-09-14",
    at: "07:30",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter",
    numRows: 90_000_000,
    durationMinutes: 212,
    quality: {},
  },
  {
    day: "2026-09-15",
    at: "10:20",
    sourceStatsTier: "exact",
    tables: ["user_features"],
    relational: false,
    numRows: 1_000_000,
    quality: { drift: 0.02 },
  },
  {
    day: "2026-09-16",
    at: "13:05",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    engine: "b2_library",
    llm: "qwen2.5-7b",
    vllmDtype: "float16",
    numRows: 90_000_000,
    durationMinutes: 198,
    quality: { corrKeep: 0.8 },
  },
  {
    day: "2026-09-17",
    at: "09:50",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter_rotate",
    embedder: "bge-small-en-v1.5",
    seed: "7",
    quality: {},
  },
  {
    day: "2026-09-18",
    at: "15:40",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter",
    env: "uat",
    similarity: 0.8,
    quality: { valueLeak: 0.003, rowLeak: 0.0002 },
  },
  // Week 8 — prd; a last in-progress run.
  {
    day: "2026-09-21",
    at: "08:10",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter",
    env: "prd",
    llm: "gemma4-26b-a4b-awq",
    quality: {},
  },
  {
    day: "2026-09-22",
    at: "11:35",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    engine: "b2_library",
    llm: "gemma4-26b-a4b-awq",
    env: "prd",
    quality: { corrKeep: 0.82 },
  },
  {
    day: "2026-09-23",
    at: "14:00",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter",
    env: "prd",
    numRows: 10_000_000,
    uniquenessMode: "streaming",
    quality: {},
  },
  {
    day: "2026-09-24",
    at: "09:25",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter",
    env: "prd",
    referenceRowsLimit: 50_000,
    numRows: 90_000_000,
    durationMinutes: 205,
    quality: {},
  },
  {
    day: "2026-09-27",
    at: "18:45",
    sourceStatsTier: "exact",
    freetextExpansion: "identifiers",
    retrieval: "kcenter",
    env: "prd",
    outcome: "RUNNING",
    trigger: "composer",
  },
];

export const STORYLINE: EvalSpec[] = ROWS.map((row, i): EvalSpec => {
  const { day, at, quality, ...rest } = row;
  const collapse = quality?.poolCollapse ?? false;
  return {
    index: i + 1,
    id: `eval-${String(i + 1).padStart(4, "0")}`,
    // Microseconds, as BigQuery stores them: every consumer must keep all six digits.
    evaluatedAt: `${day}T${at}:00.${String(((i + 1) * 104_729) % 1_000_000).padStart(6, "0")}Z`,
    durationMinutes: 38 + ((i * 17) % 31),
    trigger: "composer",
    runner: "DataflowRunner",
    mode: "exact",
    engine: "b1_rag",
    llm: "gemma4-e4b",
    embedder: "hashing-384",
    retrieval: "centroid",
    seed: "derived",
    similarity: 0.5,
    referenceRowsLimit: 10_000,
    numRows: 1_000_000,
    sourceStatsTier: "sample",
    env: "dev",
    uniquenessMode: "exact",
    freetextExpansion: collapse ? "off" : "identifiers",
    vllmDtype: "bfloat16",
    tables: THELOOK,
    relational: true,
    ...rest,
    quality: { ...CLEAN, temporalBlend: i < 20, ...quality },
  };
});

export const MODEL_URIS: Record<Llm, string> = {
  "gemma4-e4b": "gs://synthetic-platform-demo/synthetic/models/gemma4/gemma4-e4b/v1/",
  "gemma4-26b-a4b-awq": "gs://synthetic-platform-demo/synthetic/models/gemma4/gemma4-26b-a4b-awq/v1/",
  "qwen2.5-7b": "gs://synthetic-platform-demo/synthetic/models/qwen/qwen2.5-7b/v1/",
};

export const EMBEDDER_URIS: Record<EmbedderId, string> = {
  "hashing-384": "",
  "bge-small-en-v1.5": "gs://synthetic-platform-demo/synthetic/models/embedders/bge-small-en-v1.5/v1/",
};
