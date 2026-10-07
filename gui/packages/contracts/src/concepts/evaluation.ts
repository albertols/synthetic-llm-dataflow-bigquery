/**
 * EVALUATION tab concepts (`eval:` namespace). Metric concepts come from the
 * generated catalogue (`metric:<id>`, catalogue.ts); these explain the views
 * built on top of them: scope, gates, intervals, privacy panels, the compare
 * rules. Owned by the EVALUATION tab (task G2).
 */
import { defineConcepts } from "../concept";

const REPO = "https://github.com/albertols/synthetic-llm-dataflow-bigquery/blob/master";
const CATALOGUE = `${REPO}/packages/sdfb-evaluation/src/sdfb_evaluation/catalogue/metrics.yaml`;
const SCHEMAS = `${REPO}/packages/sdfb-evaluation/src/sdfb_evaluation/schemas`;
const RELATIONSHIPS = `${REPO}/config/relationships/README.md`;
const CONTRACTS = `${REPO}/gui/docs/DATA_CONTRACTS.md`;

const catalogueLink = { label: "Metric catalogue (metrics.yaml)", url: CATALOGUE, kind: "code" } as const;

export const concepts = defineConcepts([
  {
    id: "eval:evaluation",
    title: "Evaluation run",
    purpose:
      "One pass of the evaluator over one generation launch: every table of the relationship model, every metric of the catalogue, written to four BigQuery tables. The registry row (evaluation_data_history) carries the status, the scope checks and the roll-up scores.",
    interpretation: {
      good: "SUCCEEDED: every table was in scope and every metric ran.",
      bad: "PARTIAL or FAILED: read status_reason first; some metrics are missing or not evaluated.",
      tip: "SUCCEEDED_WITH_WARNINGS is operational (sampled mode, scope notes), not a metric failure — the metric counts carry those.",
    },
    links: [
      { label: "evaluation_data_history schema", url: `${SCHEMAS}/evaluation_data_history.schema.json`, kind: "code" },
      catalogueLink,
    ],
  },
  {
    id: "eval:scope",
    title: "Evaluation scope",
    purpose:
      "Which synthetic rows belong to this launch. Landing rows carry no run id, so the evaluator recovers them from the write disposition and the job's own commit window: the whole landing table (table), a snapshot at the window end (as_of), the rows appended in the window (appends), the table at the window end minus a snapshot of its start when copy jobs landed the rows (as_of_diff), or the table as it is now (manual). The scope check says whether that slice is clean.",
    interpretation: {
      good: "Scope OK: the rows in scope are exactly this launch's rows.",
      bad: "Contaminated (another run's rows share the table), count mismatch (rows ≠ rows expected), expired (the snapshot aged out) or empty: the numbers describe a different slice than the launch.",
      tip: "A contaminated scope keeps only this run's rows when it can; the note on the table says what was kept.",
    },
    links: [
      {
        label: "evaluation_data_history — tables[].scope_*",
        url: `${SCHEMAS}/evaluation_data_history.schema.json`,
        kind: "code",
      },
    ],
  },
  {
    id: "eval:reference-verified",
    title: "Reference verified",
    purpose:
      "The digest of the reference sample R the generator read matched the pinned source snapshot. Only then are R and the holdout H the generator's own sets, which the privacy lifts need.",
    interpretation: {
      good: "Verified: memorization and exposure lifts compare R against a true holdout.",
      bad: "Not verified: privacy lifts and the DCR holdout test are not evaluated — never read that as a pass.",
    },
    links: [catalogueLink],
  },
  {
    id: "eval:sampled-mode",
    title: "Sampled mode",
    purpose:
      "The evaluator read a salted row sample of each table larger than --sample_rows instead of every row, at the sample rates shown per table. A metric computed from a sampled side says method = sample with its rate, never exact, and its noise floor or interval is computed at the sampled n.",
    interpretation: {
      tip: "Sampled floors are wider: a difference that is ≈ here may be real at full scale. Re-run in exact mode before a release decision.",
    },
    pitfalls:
      "A metric that needs every row of a side (duplicate keys, orphans, exact matches against the full source, the distinct and entropy ratios, type validity) is not evaluated on a row sample; its row says why and to run exact mode. Never read that as a pass.",
    links: [catalogueLink],
  },
  {
    id: "eval:status-rule",
    title: "How a status is decided",
    purpose:
      "Thresholds are effect sizes and crossing them is inclusive (lower-is-better: WARN at g ≥ warn, FAIL at g ≥ fail). A WARN or FAIL that sampling noise explains becomes PASS, marked “≈ within noise, was FAIL” and scored at the reference, so small tables raise no false alarm.",
    formula:
      "\\text{status} = \\begin{cases} \\text{FAIL} & g \\ge f \\\\ \\text{WARN} & g \\ge w \\\\ \\text{PASS} & \\text{otherwise} \\end{cases} \\quad\\text{then}\\quad \\text{WARN/FAIL} \\to \\text{PASS}\\ (\\approx) \\text{ if } |g - r| \\le \\varepsilon \\text{ or } r \\in [\\mathrm{ci}_{\\mathrm{low}}, \\mathrm{ci}_{\\mathrm{high}}]",
    interpretation: {
      tip: "Whether noise explains a crossing depends on the metric's noise method: a scalar floor (KS, TVD, JSD, Fisher z, MI bias) must cover |g − r|; an interval method (Wilson, Newcombe, DeLong) must have its 95 % CI cover r. g is the gate value (the value, or its 95 % CI bound for lifts and the DCR share — ci_low when lower is better — even when the point value is undefined), r the noise reference (the target, else 0, or 1 when higher is better), ε the noise floor, w and f the thresholds. Higher-is-better metrics mirror the inequalities; warn 0 / fail 0 means FAIL iff g > 0, with no noise check. An infinite g past the bad side is a FAIL; a missing noise floor or CI means no downgrade (“noise check unavailable”).",
    },
    pitfalls:
      "An observed copy or invented category is an event, not an estimate: its Wilson interval never reaches the edge reference (0 or 1), so adherence shares and copy rates are never downgraded, at any n.",
    diagram: "evaluation:metric-bar",
    links: [catalogueLink],
  },
  {
    id: "eval:metric-bar",
    title: "Reading a metric bar",
    purpose:
      "One axis per metric: the grey band is the scalar noise floor around the reference, the amber and red ticks are the warn and fail thresholds, the diamond is the reference baseline and the dot the value, with its 95 % CI whisker when there is one. Interval-method metrics (Wilson, Newcombe, DeLong) have no band: their whisker reaching the reference is what noise looks like.",
    interpretation: {
      good: "Dot inside the grey band, or a whisker that reaches the reference: indistinguishable from sampling noise.",
      bad: "Dot past the red tick, outside the band and with a whisker short of the reference: a real FAIL.",
      tip: "Near the diamond means generation kept what the reference sample carried; far from it means fidelity was lost in generation.",
    },
    diagram: "evaluation:metric-bar",
    links: [catalogueLink],
  },
  {
    id: "eval:gate-ci-bound",
    title: "Gate on the CI bound",
    purpose:
      "Lifts and the DCR holdout share are ratios of small counts, so their status and score read the lower bound of the 95 % interval (ci_low), not the point estimate. A lift of 3× with ci_low 0.03 is not evidence of memorization.",
    formula: "g = \\mathrm{ci}_{\\mathrm{low}} \\quad (\\text{uses\\_ci\\_bound})",
    interpretation: {
      good: "ci_low under the warn threshold: the data do not show a lift at 95 %.",
      bad: "ci_low at or above the threshold: even the most favourable reading shows the lift.",
      tip: "With no copies on either side the lift itself is undefined, but its interval is (0, ∞): the gate reads ci_low 0 and a clean run passes with score 1.",
    },
    links: [
      {
        label: "Clopper & Pearson 1934 — exact binomial interval",
        url: "https://doi.org/10.1093/biomet/26.4.404",
        kind: "paper",
      },
      catalogueLink,
    ],
  },
  {
    id: "eval:lift",
    title: "Memorization lift (rate ratio)",
    purpose:
      "How much more often synthetic rows copy the reference sample R than an equally sized holdout H the generator never saw. 1 means no memorization beyond chance agreement.",
    formula: "\\text{lift} = \\frac{m_R / n_R}{m_H / n_H}",
    interpretation: {
      good: "ci_low below 2: no evidence that the generator copies what it read.",
      bad: "ci_low at or above 5: the generator reproduces its reference rows or values.",
      tip: "When m_H = 0 the point estimate is infinite, and with m_R = m_H = 0 it is undefined; the interval (Clopper–Pearson on m_R | m_R + m_H) still bounds it, and the status reads that bound.",
    },
    pitfalls:
      "A lift is a risk indicator, not a privacy guarantee: a lift of 1 says nothing about attacks that do not need exact copies.",
    links: [
      { label: "Clopper & Pearson 1934", url: "https://doi.org/10.1093/biomet/26.4.404", kind: "paper" },
      {
        label: "Stadler et al. 2022 — Anonymisation Groundhog Day",
        url: "https://www.usenix.org/conference/usenixsecurity22/presentation/stadler",
        kind: "paper",
      },
    ],
  },
  {
    id: "eval:privacy-risk",
    title: "Risk indicators, not guarantees",
    purpose:
      "Copy rates, lifts, DCR and NNDR measure how close synthetic rows sit to real ones. They flag risk; they do not prove privacy, which only a formal mechanism such as differential privacy can.",
    interpretation: {
      good: "All clear: no evidence of copying at this n. Keep the flagged-row review.",
      bad: "A fail is actionable: memorized rows or values leak from the reference sample.",
      tip: "Read the lifts (reference vs holdout) before the raw rates: a raw copy rate also counts values that are simply common.",
    },
    pitfalls: "Similarity-based metrics can pass while a membership-inference or linkage attack still succeeds.",
    links: [
      {
        label: "Stadler et al. 2022 — Anonymisation Groundhog Day",
        url: "https://www.usenix.org/conference/usenixsecurity22/presentation/stadler",
        kind: "paper",
      },
      {
        label: "Ganev & De Cristofaro 2023 — similarity-based privacy metrics",
        url: "https://arxiv.org/abs/2312.05114",
        kind: "paper",
      },
    ],
  },
  {
    id: "eval:dcr",
    title: "Distance to closest record (DCR)",
    purpose:
      "For each synthetic row, the Gower distance (0–1) to its nearest reference row, next to the same distance for holdout rows. Synthetic rows much closer to R than holdout rows are to R suggest copying — a risk indicator, not a guarantee.",
    formula: "\\mathrm{DCR}(y) = \\min_{x \\in R} d_{\\mathrm{Gower}}(y, x)",
    interpretation: {
      good: "The syn→R and H→R histograms overlap: synthetic rows are as far from R as unseen real rows.",
      bad: "A spike near 0 on syn→R only: near-copies of reference rows.",
    },
    pitfalls: "A good DCR does not rule out attribute inference; it is a similarity measure.",
    links: [
      { label: "Park et al. 2018 — table-GAN (DCR)", url: "https://doi.org/10.14778/3231751.3231757", kind: "paper" },
      {
        label: "Platzer & Reutterer 2021 — holdout-based assessment",
        url: "https://doi.org/10.3389/fdata.2021.679939",
        kind: "paper",
      },
      {
        label: "Gower 1971 — general coefficient of similarity",
        url: "https://doi.org/10.2307/2528823",
        kind: "paper",
      },
    ],
  },
  {
    id: "eval:nndr",
    title: "Nearest-neighbour distance ratio (NNDR)",
    purpose:
      "Distance to the nearest reference row divided by the distance to the second nearest. Values near 0 mean a synthetic row sits on top of one real row, away from the rest — a risk indicator, not a guarantee.",
    formula: "\\mathrm{NNDR}(y) = \\frac{d_{(1)}(y, R)}{d_{(2)}(y, R)}",
    interpretation: {
      good: "syn→R close to H→R, both near 1: rows sit among many real neighbours.",
      bad: "A mass near 0 on syn→R only: outliers were reproduced.",
    },
    links: [
      { label: "Zhao et al. 2021 — CTAB-GAN (DCR, NNDR)", url: "https://arxiv.org/abs/2102.08369", kind: "paper" },
      {
        label: "Ganev & De Cristofaro 2023 — limits of similarity metrics",
        url: "https://arxiv.org/abs/2312.05114",
        kind: "paper",
      },
    ],
  },
  {
    id: "eval:holdout-share",
    title: "DCR holdout share",
    purpose:
      "Share of synthetic rows whose closest record is in R rather than in an equally sized holdout H. A generator that did not memorize sits at 0.5; the gate reads the Wilson lower bound — a risk indicator, not a guarantee.",
    formula:
      "s = \\frac{\\#\\{y : d(y, R) < d(y, H)\\}}{|Y|},\\qquad g = \\mathrm{ci}_{\\mathrm{low}}^{\\mathrm{Wilson}}(s)",
    interpretation: {
      good: "ci_low ≤ 0.55: consistent with no preference for the reference rows.",
      bad: "ci_low ≥ 0.6: synthetic rows prefer the rows the generator read.",
    },
    diagram: "evaluation:holdout-share",
    links: [
      {
        label: "Platzer & Reutterer 2021 — holdout DCR share",
        url: "https://doi.org/10.3389/fdata.2021.679939",
        kind: "paper",
      },
      { label: "Wilson 1927 — score interval", url: "https://doi.org/10.1080/01621459.1927.10502953", kind: "paper" },
    ],
  },
  {
    id: "eval:row-flags",
    title: "Flagged rows",
    purpose:
      "The most extreme synthetic rows per check (exact copy, near copy, nearest record, detectable), identified by their synthetic keys. The matched source record is a keyed hash (h: and eight hex digits); raw source keys are never written.",
    interpretation: {
      tip: "Look up a flagged synthetic key in the landing table. The hash names one source record within an evaluation; two evaluations agree on it only when both ran with the operator's label key (--label_key_uri), because the default key is ephemeral.",
    },
    links: [{ label: "evaluation_row_flags schema", url: `${SCHEMAS}/evaluation_row_flags.schema.json`, kind: "code" }],
  },
  {
    id: "eval:wilson",
    title: "Wilson interval",
    purpose:
      "A 95 % interval for a proportion k/n that stays inside [0, 1] and behaves at k = 0. Two proportions whose intervals overlap widely are not told apart at this n.",
    formula:
      "\\frac{\\hat p + \\frac{z^2}{2n} \\pm z\\sqrt{\\frac{\\hat p(1-\\hat p)}{n} + \\frac{z^2}{4n^2}}}{1 + \\frac{z^2}{n}}",
    links: [
      { label: "Wilson 1927", url: "https://doi.org/10.1080/01621459.1927.10502953", kind: "paper" },
      {
        label: "Newcombe 1998 — difference of proportions",
        url: "https://doi.org/10.1002/(SICI)1097-0258(19980430)17:8%3C873::AID-SIM779%3E3.0.CO;2-I",
        kind: "paper",
      },
    ],
  },
  {
    id: "eval:ks-bracket",
    title: "KS bracket (binned)",
    purpose:
      "From bin counts alone the true KS distance is only known within a bracket: the largest CDF gap at the bin edges is a lower bound, and the gap the mass inside one bin could hide is an upper bound. The catalogue value is the lower bound.",
    formula: "d_{\\mathrm{lo}} = \\max_{e} |F_s(e) - F_y(e)| \\;\\le\\; D_{\\mathrm{KS}} \\;\\le\\; d_{\\mathrm{hi}}",
    interpretation: {
      tip: "A wide bracket means the bins are coarse where the CDFs differ; the ECDF marker sits at the edge of the largest gap.",
    },
    diagram: "evaluation:ks-bracket",
    links: [
      { label: "Smirnov 1948 — two-sample KS", url: "https://doi.org/10.1214/aoms/1177730256", kind: "paper" },
      { label: "Massart 1990 — DKW constant", url: "https://doi.org/10.1214/aop/1176990746", kind: "paper" },
    ],
  },
  {
    id: "eval:ecdf",
    title: "Empirical CDF with a noise band",
    purpose:
      "The share of values at or below each bin edge, source against synthetic. The grey band is the source CDF ± the KS noise floor: a synthetic curve inside it is indistinguishable at this n.",
    formula: "F_n(t) = \\frac{1}{n} \\sum_i \\mathbf{1}[x_i \\le t]",
    links: [{ label: "Massart 1990 — DKW inequality", url: "https://doi.org/10.1214/aop/1176990746", kind: "paper" }],
  },
  {
    id: "eval:qq-plot",
    title: "Q–Q plot",
    purpose:
      "Synthetic quantiles against source quantiles at the same probabilities. Points on the diagonal mean the two distributions agree; a bend shows where they part.",
    interpretation: {
      tip: "Points above the diagonal in the right tail: the synthetic tail is heavier. A flat run of points: a value repeated where the source spreads.",
    },
    links: [
      {
        label: "Wilk & Gnanadesikan 1968 — probability plots",
        url: "https://doi.org/10.1093/biomet/55.1.1",
        kind: "paper",
      },
    ],
  },
  {
    id: "eval:matched-n",
    title: "Why matched n",
    purpose:
      "Entropy and distinct counts grow with the number of rows read, so a 90-million-row synthetic table always looks more diverse than a 100,000-row source. The evaluator compares both sides at the same n, the smaller side's row count.",
    formula: "\\hat H_{\\mathrm{plug\\text{-}in}}(n) \\approx H - \\frac{K - 1}{2n}",
    interpretation: {
      good: "Ratio near 1 at matched n: the generator reproduces the source's diversity.",
      bad: "Below 1: mode collapse or a capped pool; above 1: invented values.",
    },
    diagram: "evaluation:matched-n",
    links: [
      {
        label: "Paninski 2003 — entropy estimation bias",
        url: "https://doi.org/10.1162/089976603321780272",
        kind: "paper",
      },
      catalogueLink,
    ],
  },
  {
    id: "eval:hashed-labels",
    title: "Hashed labels (literal policy D6)",
    purpose:
      "A category is shown literally only when its column has at most 50 source-distinct values and the value occurs at least 10 times in the source. Everything else is a keyed hash h:xxxxxxxx, so rare values never leave the evaluator.",
    interpretation: { tip: "Hashes are stable within an evaluation, so source and synthetic bars still line up." },
    links: [{ label: "GUI data contracts — payload shapes", url: CONTRACTS, kind: "docs" }],
  },
  {
    id: "eval:detection",
    title: "Detection ROC",
    purpose:
      "A classifier tries to tell synthetic rows from source rows; its ROC curve and AUC measure how detectable the synthetic data is. AUC 0.5 is indistinguishable, 1.0 trivially separable.",
    formula: "\\mathrm{AUC} = P(\\hat p(y) > \\hat p(x)),\\ y \\in \\text{syn},\\ x \\in \\text{src}",
    interpretation: {
      good: "Curve on the diagonal, AUC band touching 0.5.",
      bad: "AUC ≥ 0.85: the classifier separates the sets easily — some column or joint pattern gives the data away.",
      tip: "The band is the binormal ROC at the AUC interval's bounds (DeLong, or Hanley–McNeil when per-row scores are not stored).",
    },
    links: [
      { label: "DeLong et al. 1988 — comparing AUCs", url: "https://doi.org/10.2307/2531595", kind: "paper" },
      {
        label: "Hanley & McNeil 1982 — AUC standard error",
        url: "https://doi.org/10.1148/radiology.143.1.7063747",
        kind: "paper",
      },
      { label: "Snoke et al. 2018 — pMSE utility", url: "https://doi.org/10.1111/rssa.12358", kind: "paper" },
    ],
  },
  {
    id: "eval:model-graph",
    title: "Model graph",
    purpose:
      "The relationship model of the launch: one node per table, coloured by its overall score, one arrow per foreign key from child to parent, marked by its orphan and fan-out status. Click a table to scope the views below to it.",
    interpretation: {
      tip: "Dashed nodes are external parents the launch did not generate; dashed arrows are documented edges (enforced: false).",
    },
    links: [{ label: "Relationship models — README", url: RELATIONSHIPS, kind: "docs" }],
  },
  {
    id: "eval:documented-edge",
    title: "Documented edge",
    purpose:
      "A foreign key declared with enforced: false: the launch does not generate children from it, so its orphan rate is reported as INFO next to the source's own orphan rate — never as a FAIL.",
    interpretation: {
      tip: "Compare the synthetic orphan rate with orphan_rate_source: a source with deleted parents has orphans too.",
    },
    links: [{ label: "Relationship models — enforced and enabled", url: RELATIONSHIPS, kind: "docs" }],
  },
  {
    id: "eval:fanout",
    title: "Fan-out",
    purpose:
      "Children per parent along a foreign key: how many orders each user has. The evaluator compares the source and synthetic distributions (every parent at or above the cap in one last bin) and stores the distances: the fan-out TVD and W1, the mean ratio, the childless-parent share and the parent coverage. It publishes no fan-out histogram.",
    interpretation: {
      good: "Fan-out TVD within its noise floor, mean ratio near 1, childless-parent share unchanged.",
      bad: "A childless-parent share that drops to 0: every synthetic parent got a child the source's parents often lack.",
    },
    links: [{ label: "Relationship models — README", url: RELATIONSHIPS, kind: "docs" }],
  },
  {
    id: "eval:info-status",
    title: "INFO and other statuses",
    purpose:
      "An INFO row is measured but not graded: the metric has no thresholds (Wasserstein in column units, fan-out W1, the source's own orphan rate), it is the orphan rate of a documented foreign key (enforced: false), or it is the copy rate of a column that is not free text (numeric and temporal values collide with a dense source by domain size, and reusing a rare real category or identifier is not evidence of memorization). INFO rows count in every total, never pass or fail, and carry no score.",
    interpretation: {
      tip: "“Other status” counts rows whose status is newer than this GUI's vocabulary; they are shown as plain text wherever they appear. Every breakdown here adds up to its total.",
    },
    links: [catalogueLink],
  },
  {
    id: "eval:heatmap",
    title: "Column × metric heatmap",
    purpose:
      "One row per column and one cell per field- or column-level metric, worst rows first. Each cell shows the status icon and the raw value; open a row for its drawer.",
    interpretation: {
      tip: "An empty cell means the metric does not apply to that column kind; “n/e” means it applied but was not evaluated (the drawer gives the reason).",
    },
    links: [catalogueLink],
  },
  {
    id: "eval:interpretation",
    title: "Automatic interpretation",
    purpose:
      "Sentences built from each metric's catalogue interpretation, its status, its noise floor and its reference baseline. They rank what to look at first; the numbers next to them are the evidence.",
    interpretation: {
      tip: "“Indistinguishable at this n” means below the noise floor, not zero; a larger n could still find a difference.",
    },
    links: [catalogueLink],
  },
  {
    id: "eval:correlation-delta",
    title: "Correlation matrices",
    purpose:
      "Pearson (numeric) correlations of the source and of the synthetic table on a paired sample, and their difference. Joint structure lives here: marginals can all pass while every correlation is lost.",
    formula: "\\Delta_{ij} = r^{\\mathrm{syn}}_{ij} - r^{\\mathrm{src}}_{ij}",
    interpretation: {
      good: "Δ near 0 everywhere (within the Fisher-z noise floor).",
      bad: "|Δ| ≥ 0.2 on a pair: the generator broke that dependence.",
    },
    links: [{ label: "Fisher 1915 — z-transform of r", url: "https://doi.org/10.2307/2331838", kind: "paper" }],
  },
  {
    id: "eval:contingency",
    title: "Contingency tables",
    purpose:
      "Joint category shares of two columns, source against synthetic. The pairs with the largest total variation are shown; the Δ map says which combinations were over- or under-produced.",
    formula: "\\mathrm{TV} = \\tfrac12 \\sum_{i,j} |p_{ij} - q_{ij}|",
    links: [catalogueLink],
  },
  {
    id: "eval:comparable",
    title: "Comparable runs",
    purpose:
      "Two evaluations are directly comparable only with the same catalogue version, the same evaluator version and, per table, the same encoding plan (bins, dictionaries, pairs). Otherwise a delta may measure the ruler, not the data.",
    interpretation: {
      tip: "Not comparable does not mean useless: statuses are still each run's own verdict. Deltas between them are what you cannot trust.",
    },
    links: [catalogueLink],
  },
  {
    id: "eval:approx",
    title: "≈ (within noise)",
    purpose:
      "A difference between two runs smaller than the larger of their noise floors, or two confidence intervals that overlap. It cannot be told apart from sampling noise, so it is shown as ≈, never as better or worse.",
    formula: "|v_B - v_A| \\le \\max(\\varepsilon_A, \\varepsilon_B) \\Rightarrow \\approx",
    diagram: "core:noise-floor",
    links: [
      { label: "Massart 1990 — DKW", url: "https://doi.org/10.1214/aop/1176990746", kind: "paper" },
      catalogueLink,
    ],
  },
  {
    id: "eval:encoding-plan",
    title: "Encoding plan digest",
    purpose:
      "A digest of how the evaluator encoded a table: bin edges, category dictionaries, the column pairs it profiled. Two runs with different digests measured with different rulers.",
    links: [{ label: "evaluation_metrics schema", url: `${SCHEMAS}/evaluation_metrics.schema.json`, kind: "code" }],
  },
  {
    id: "eval:pareto",
    title: "Fidelity–privacy frontier",
    purpose:
      "Each run as a point (fidelity score, privacy score). A run is on the frontier when no other run is at least as good on both and better on one.",
    interpretation: {
      tip: "Moving along the frontier trades fidelity for privacy; a run inside it is beaten on both by a frontier run.",
    },
    links: [
      {
        label: "Goncalves et al. 2020 — utility vs privacy of synthetic data",
        url: "https://doi.org/10.1186/s12874-020-00977-1",
        kind: "paper",
      },
    ],
  },
  {
    id: "eval:parallel",
    title: "Parameters → scores",
    purpose:
      "One polyline per run across its generation parameters and its family scores. Lines that bundle on a parameter value and end high on a score suggest the setting that helps.",
    interpretation: {
      tip: "Brush an axis to keep only the runs in a range; correlation is not causation with a handful of runs.",
    },
    links: [
      { label: "Inselberg 1985 — parallel coordinates", url: "https://doi.org/10.1007/BF01898350", kind: "paper" },
    ],
  },
  {
    id: "eval:saved-views",
    title: "Saved views",
    purpose:
      "Every filter lives in the page URL, so a view is saved by bookmarking or sharing the link. Named views are kept in this browser only.",
    links: [{ label: "GUI UX rules — filters in the URL", url: `${REPO}/gui/docs/UX.md`, kind: "docs" }],
  },
  {
    id: "eval:catalogue-version",
    title: "Catalogue version",
    purpose:
      "The metric catalogue the evaluation applied. The (i) hints here come from the catalogue this GUI was built with; when the evaluation used another version, its stored statuses and thresholds win.",
    links: [catalogueLink],
  },
]);
