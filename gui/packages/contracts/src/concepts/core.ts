/**
 * Foundation concepts: the vocabulary every tab shares (levels, status, score,
 * noise floor, baseline, data source). Owned by the foundation (G0a/G0b);
 * tab concepts live in their own file next to this one.
 */
import { defineConcepts } from "../concept";

const REPO = "https://github.com/albertols/synthetic-llm-dataflow-bigquery/blob/master";
const CATALOGUE = `${REPO}/packages/sdfb-evaluation/src/sdfb_evaluation/catalogue/metrics.yaml`;
const ADR_0042 = `${REPO}/docs/adr/0042-self-hosted-platform-gui.md`;

const levelLink = { label: "Metric catalogue — levels", url: CATALOGUE, kind: "code" } as const;

export const concepts = defineConcepts([
  {
    id: "core:noise-floor",
    title: "Noise floor",
    purpose:
      "The smallest metric value that sampling noise alone produces at this sample size. A difference below it cannot be told apart from drawing the same data twice.",
    formula: "\\varepsilon_{\\mathrm{DKW}}(n) = \\sqrt{\\frac{\\ln(2/\\alpha)}{2n}}",
    interpretation: {
      good: "Below the floor: the synthetic data is as close as sampling allows at this n.",
      bad: "Well above the floor: a real difference. Read it against the warn and fail thresholds.",
      tip: "Floors shrink as 1/√n. At n = 10,000 and α = 0.05 the DKW band is ε ≈ 0.0136.",
    },
    pitfalls:
      "A FAIL needs the value to clear its noise floor. At n = 10⁷ a p-value flags every difference, so the catalogue thresholds are effect sizes, never p-values.",
    diagram: "core:noise-floor",
    links: [
      { label: "Massart 1990 — tight DKW constant", url: "https://doi.org/10.1214/aop/1176990746", kind: "paper" },
      { label: "Smirnov 1948 — two-sample KS", url: "https://doi.org/10.1214/aoms/1177730256", kind: "paper" },
      { label: "Metric catalogue — noise_floor methods", url: CATALOGUE, kind: "code" },
    ],
  },
  {
    id: "core:baseline",
    title: "Reference baseline",
    purpose:
      "The metric computed between the reference sample R the generator read and the full source. It is the score a perfect copier of R would get.",
    formula: "b = \\operatorname{metric}(R,\\ \\mathrm{src})",
    interpretation: {
      good: "Synthetic value near the baseline: generation kept everything the sample carried.",
      bad: "Far above the baseline: fidelity was lost in generation, not in sampling.",
      tip: "A high baseline means the reference sample is too small; raise reference_rows_limit.",
    },
    links: [{ label: "Metric catalogue — baseline", url: CATALOGUE, kind: "code" }],
  },
  {
    id: "core:score",
    title: "Score (0 – 1)",
    purpose:
      "Maps a metric's raw value to [0, 1], higher is better, with one of the catalogue's five score functions; a metric that uses its CI bound scores ci_low instead of the value. Family and overall scores are means over units (columns, the pair group, rows, edges).",
    formula:
      "s = \\begin{cases} \\operatorname{clip}(1 - |v| / r_{\\mathrm{hi}},\\ 0,\\ 1) & \\text{complement} \\\\ \\operatorname{clip}\\!\\left(\\frac{f - v}{f - w},\\ 0,\\ 1\\right) & \\text{linear (lower better; mirrored)} \\\\ \\operatorname{clip}\\!\\left(\\frac{f - d}{f - w},\\ 0,\\ 1\\right),\\ d = |v - t| & \\text{ratio to one} \\\\ 1 - 2\\max(0,\\ v - 0.5) & \\text{AUC} \\\\ v \\text{ if } 0 \\le v \\le 1 \\text{, else null} & \\text{none (roll-ups)} \\end{cases}",
    interpretation: {
      good: "1.0: at or better than the warn threshold (w), or a detection AUC at or below 0.5.",
      bad: "0.0: at or past the fail threshold (f).",
      tip: "w = warn threshold, f = fail threshold, t = target, r_hi = top of the range. Compare raw values with the noise floor before reading a score change.",
    },
    pitfalls:
      "A score hides the unit. Two runs can score the same while one sits on the noise floor and the other does not.",
    links: [{ label: "Metric catalogue — score functions", url: CATALOGUE, kind: "code" }],
  },
  {
    id: "core:status",
    title: "Metric status",
    purpose:
      "PASS, WARN, FAIL, INFO or NOT EVALUATED, from the catalogue thresholds. Crossing a threshold is inclusive, and a FAIL also needs the value to clear its noise floor.",
    interpretation: {
      good: "PASS: better than the warn threshold.",
      bad: "FAIL: at or past the fail threshold and above the noise floor.",
      tip: "NOT EVALUATED means the metric could not run (for example, an unverified reference), not that it passed.",
    },
    links: [{ label: "Metric catalogue — thresholds", url: CATALOGUE, kind: "code" }],
  },
  {
    id: "core:family",
    title: "Metric family",
    purpose:
      "Every measured metric belongs to one family: fidelity, privacy, integrity or diversity. The overall score is the mean of the available family scores.",
    links: [{ label: "Metric catalogue — families", url: CATALOGUE, kind: "code" }],
  },
  {
    id: "core:data-source",
    title: "Data source",
    purpose:
      "MOCK serves seeded fixtures with invented or public thelook names, so the app runs with no GCP access. BIGQUERY reads this project's tables through named, read-only, bytes-capped queries.",
    interpretation: {
      tip: "The browser never sends SQL and never holds credentials; the local BFF does both jobs.",
    },
    links: [{ label: "ADR 0042 — self-hosted platform GUI", url: ADR_0042, kind: "adr" }],
  },
  {
    id: "core:level-field",
    title: "Field level",
    purpose:
      "Value by value: does each synthetic value obey its column's rules (category set, range, shape, type), and how often does it copy a source value?",
    level: "field",
    diagram: "core:levels",
    links: [levelLink],
  },
  {
    id: "core:level-column",
    title: "Column level",
    purpose: "One column's distribution against the source: shape, quantiles, categories, nulls and entropy.",
    level: "column",
    diagram: "core:levels",
    links: [levelLink],
  },
  {
    id: "core:level-pair",
    title: "Pair level",
    purpose: "Two columns together: correlations and contingency tables the marginals alone cannot show.",
    level: "pair",
    diagram: "core:levels",
    links: [levelLink],
  },
  {
    id: "core:level-row",
    title: "Row level",
    purpose:
      "Whole rows: exact and near matches, memorization and exposure lifts, distance to the closest record, coverage and null patterns.",
    level: "row",
    diagram: "core:levels",
    links: [levelLink],
  },
  {
    id: "core:level-table",
    title: "Table level",
    purpose:
      "One table as a whole: row counts, key duplicates, classifier detection, correlation drift and the table's family scores.",
    level: "table",
    diagram: "core:levels",
    links: [levelLink],
  },
  {
    id: "core:level-relationship",
    title: "Relationship level",
    purpose:
      "One foreign-key edge: orphan rate, fan-out distribution, zero-child share, cardinality and parent coverage against the source.",
    level: "relationship",
    diagram: "core:levels",
    links: [levelLink],
  },
  {
    id: "core:level-model",
    title: "Model level",
    purpose: "The whole relationship model: the mean of its tables' family scores.",
    level: "model",
    diagram: "core:levels",
    links: [levelLink],
  },
]);
