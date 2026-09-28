/**
 * RAG tab concepts (`rag:` only): the b1_rag layer as the code runs it —
 * chunks, embedders, the exact index, seed strategies, pools — and the
 * projection/measure vocabulary the tab's views use. Values quoted here come
 * from the code (knobs.json); where docs disagree the tab shows a
 * "Docs differ" note, not a concept.
 */
import { defineConcepts, type ConceptLink } from "../concept";

const REPO = "https://github.com/albertols/synthetic-llm-dataflow-bigquery/blob/master";
const code = (label: string, path: string): ConceptLink => ({ label, url: `${REPO}/${path}`, kind: "code" });
const adr = (label: string, file: string): ConceptLink => ({ label, url: `${REPO}/docs/adr/${file}`, kind: "adr" });

const RETRIEVAL = code("rag/retrieval.py — select_seed_examples", "packages/sdfb-core/src/sdfb_core/rag/retrieval.py");
const INDEX = code("rag/index.py — build_index", "packages/sdfb-core/src/sdfb_core/rag/index.py");
const EMBEDDING = code(
  "rag/embedding.py — HashingEmbedder, BgeEmbedder",
  "packages/sdfb-core/src/sdfb_core/rag/embedding.py",
);
const SERIALIZE = code("rag/serialize.py — serialize_row", "packages/sdfb-core/src/sdfb_core/rag/serialize.py");
const CHUNKING = code("rag/chunking.py — chunk kinds and caps", "packages/sdfb-core/src/sdfb_core/rag/chunking.py");
const ENGINE = code("b1_rag/engine.py — pools and seeds", "packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py");
const ARTICLE = {
  label: "Article 4 — b1_rag deep dive",
  url: `${REPO}/docs/articles/04-b1-rag-deep-dive.md`,
  kind: "docs",
} as const satisfies ConceptLink;
const GREAT: ConceptLink = {
  label: "Borisov et al. 2023 — GReaT (ICLR)",
  url: "https://arxiv.org/abs/2210.06280",
  kind: "paper",
};
const FAISS: ConceptLink = {
  label: "Johnson, Douze, Jégou 2017 — billion-scale search with GPUs (FAISS)",
  url: "https://arxiv.org/abs/1702.08734",
  kind: "paper",
};
const FAISS_WIKI: ConceptLink = {
  label: "FAISS wiki — guidelines to choose an index",
  url: "https://github.com/facebookresearch/faiss/wiki/Guidelines-to-choose-an-index",
  kind: "docs",
};
const GONZALEZ: ConceptLink = {
  label: "Gonzalez 1985 — clustering to minimize the maximum intercluster distance",
  url: "https://doi.org/10.1016/0304-3975(85)90224-5",
  kind: "paper",
};
const SENER: ConceptLink = {
  label: "Sener & Savarese 2018 — core-sets by k-center (ICLR)",
  url: "https://arxiv.org/abs/1708.00489",
  kind: "paper",
};
const MMR: ConceptLink = {
  label: "Carbonell & Goldstein 1998 — MMR (SIGIR)",
  url: "https://doi.org/10.1145/290941.291025",
  kind: "paper",
};
const BGE_CARD: ConceptLink = {
  label: "BAAI/bge-small-en-v1.5 model card",
  url: "https://huggingface.co/BAAI/bge-small-en-v1.5",
  kind: "docs",
};
const CPACK: ConceptLink = {
  label: "Xiao et al. 2024 — C-Pack / bge (SIGIR)",
  url: "https://arxiv.org/abs/2309.07597",
  kind: "paper",
};
const HASHING: ConceptLink = {
  label: "Weinberger et al. 2009 — feature hashing",
  url: "https://arxiv.org/abs/0902.2206",
  kind: "paper",
};
const UMAP: ConceptLink = {
  label: "McInnes, Healy, Melville 2018 — UMAP",
  url: "https://arxiv.org/abs/1802.03426",
  kind: "paper",
};
const PEARSON: ConceptLink = {
  label: "Pearson 1901 — lines and planes of closest fit (PCA)",
  url: "https://doi.org/10.1080/14786440109462720",
  kind: "paper",
};
const VENNA: ConceptLink = {
  label: "See also: Venna & Kaski 2001 — trustworthiness (rank-penalised, a related measure)",
  url: "https://doi.org/10.1007/3-540-44668-0_68",
  kind: "paper",
};
const LEWIS: ConceptLink = {
  label: "Lewis et al. 2020 — retrieval-augmented generation",
  url: "https://arxiv.org/abs/2005.11401",
  kind: "paper",
};
const TABGEN: ConceptLink = {
  label: "Fang et al. 2025 — TabGen-ICL: which examples you show matters",
  url: "https://arxiv.org/abs/2502.16414",
  kind: "paper",
};

export const concepts = defineConcepts([
  {
    id: "rag:overview",
    title: "The b1_rag layer",
    purpose:
      "Retrieval that seeds a prompt and never touches a row: once per free-text column the engine picks eight real values for the LLM to see. Everything on this page exists to make that one choice well and reproducibly.",
    interpretation: {
      tip: "Follow the numbered sections: vectors, how a row becomes one, the index, the eight seeds, the pools they produce.",
    },
    diagram: "rag:pipeline",
    links: [
      ARTICLE,
      LEWIS,
      adr("ADR 0017 — a custom RAG layer over apache_beam.ml.rag", "0017-custom-rag-layer-over-beam-ml-rag.md"),
    ],
  },
  {
    id: "rag:embedding-explorer",
    title: "Embedding explorer",
    purpose:
      "The loaded 384-d vectors drawn in three dimensions, so clusters, outliers and the seeds' spread become visible. The picture is a projection; every number beside it is measured in the full space.",
    interpretation: {
      tip: "Judge the picture by its k-NN overlap: low means neighbours in the drawing are often not neighbours in the data.",
    },
    pitfalls:
      "Distances in a projection lie, UMAP's more than PCA's. Read cosines in the inspector, not gaps on screen.",
    links: [PEARSON, UMAP, VENNA],
  },
  {
    id: "rag:pca",
    title: "PCA projection",
    purpose:
      "The three directions of largest variance, found by power iteration: linear, deterministic, and exact for a new vector such as your query. Its share of variance says how much of the space the picture keeps.",
    formula:
      "w_1 = \\arg\\max_{\\lVert w\\rVert = 1} \\operatorname{Var}(Xw), \\quad y_i = (x_i - \\bar{x})^{\\top}[w_1\\ w_2\\ w_3]",
    interpretation: {
      good: "A large share: the picture keeps much of the spread.",
      bad: "A few percent: the space is nearly isotropic, as it is for the hashing embedder's random buckets.",
    },
    links: [PEARSON],
  },
  {
    id: "rag:umap",
    title: "UMAP projection",
    purpose:
      "A non-linear layout that keeps local neighbourhoods (15 neighbours, min_dist 0.1, seed 42), fitted in a web worker; the BFF keeps the finished layout in memory for the next visit. A query is placed at the similarity-weighted mean of its neighbours, an approximation.",
    interpretation: {
      tip: "Clusters that stay apart in UMAP but merge in PCA are real local structure; distances between clusters mean little.",
    },
    pitfalls: "UMAP's global geometry (sizes, gaps) is not faithful. Use it to see groups, not to read distances.",
    links: [UMAP],
  },
  {
    id: "rag:knn-overlap",
    title: "k-NN overlap",
    purpose:
      "The share of each point's k nearest neighbours in 384-d that remain among its k nearest in the projection (k = 10, averaged over 150 evenly spaced points). It says how far to trust what looks close on screen.",
    formula: "O = \\frac{1}{m}\\sum_{i=1}^{m} \\frac{\\lvert N_{10}^{\\,384}(i) \\cap N_{10}^{\\,3}(i)\\rvert}{10}",
    interpretation: {
      good: "High (a rough guide: above half): neighbourhoods on screen are mostly real.",
      bad: "Low (a rough guide: below a fifth): the picture scrambles neighbourhoods; rely on the inspector's cosines.",
      tip: "For unit vectors Euclidean and cosine neighbours are the same, so either metric gives this number.",
    },
    pitfalls:
      "A plain set overlap: it counts how many neighbours survive, not how far they moved. Trustworthiness (Venna & Kaski) additionally penalises intruders by their rank; it is not what is shown here.",
    diagram: "rag:knn-overlap",
    links: [VENNA],
  },
  {
    id: "rag:clusters",
    title: "Clusters (spherical k-means, k = 3)",
    purpose:
      "Three groups found in the full space by cosine k-means, used to colour the cloud and to count how many groups a strategy's seeds reach. Three because a point cloud only keeps three colours distinguishable for every reader.",
    formula:
      "c(i) = \\arg\\max_{c}\\ \\cos(x_i, \\mu_c), \\qquad \\mu_c = \\frac{\\sum_{i \\in c} x_i}{\\lVert \\sum_{i \\in c} x_i \\rVert}",
    interpretation: {
      tip: "Colour by cluster, then switch PCA ↔ UMAP: a projection that mixes the colours is hiding structure.",
    },
    links: [
      {
        label: "Dhillon & Modha 2001 — spherical k-means",
        url: "https://doi.org/10.1023/A:1007612920971",
        kind: "paper",
      },
    ],
  },
  {
    id: "rag:chunk-kinds",
    title: "Chunk kinds: row documents and value chunks",
    purpose:
      "rag_chunks holds two kinds: a row_doc is one reference row as a GReaT sentence (the first 1,024 rows only), and a free_text_col chunk is one distinct value of one free-text column (at most 1,024 per column). The row vectors form the row index; one column's value vectors form the column index that picks the seeds.",
    interpretation: {
      tip: "Pick a column's values as the space to see exactly what the seed strategies choose from.",
    },
    links: [
      CHUNKING,
      adr("ADR 0019 — population scoped to its consumers", "0019-rag-population-scoped-to-consumers.md"),
    ],
  },
  {
    id: "rag:reference-digest",
    title: "Reference sample (digest)",
    purpose:
      "The SHA-256 over the whole fingerprint-ordered reference sample; it keys rag_chunks and freetext_pools, so a new sample is a new vector set and a new pool. Same rows, same digest, same vectors, same seeds.",
    links: [
      adr("ADR 0005 — live SELECT reference data", "0005-live-select-reference-data.md"),
      code("rag/chunking.py — compute_row_digest", "packages/sdfb-core/src/sdfb_core/rag/chunking.py"),
    ],
  },
  {
    id: "rag:cosine",
    title: "Cosine similarity",
    purpose:
      "The cosine of the angle between two vectors; on L2-normalised vectors it equals their inner product, which is why an inner-product index ranks by cosine exactly. 1 is the same direction, 0 unrelated, −1 opposite.",
    formula:
      "\\cos(u, v) = \\frac{u \\cdot v}{\\lVert u \\rVert\\,\\lVert v \\rVert} = u \\cdot v \\quad \\text{when } \\lVert u\\rVert = \\lVert v \\rVert = 1",
    interpretation: {
      tip: "With the hashing embedder, values within about ±0.05 of 0 are collision noise; with bge, compare cosines only against each other.",
    },
    diagram: "rag:unit-sphere",
    links: [
      FAISS,
      { label: "Reimers & Gurevych 2019 — Sentence-BERT", url: "https://arxiv.org/abs/1908.10084", kind: "paper" },
    ],
  },
  {
    id: "rag:query-box",
    title: "Query the space",
    purpose:
      "Your text is embedded in the browser by the exact port of HashingEmbedder and ranked against every loaded vector by cosine, the exact index's order. The pipeline itself never asks a user's query: its only question is the centroid.",
    interpretation: {
      tip: "The hashing embedder is lexical: “city is Pine” finds rows containing those exact tokens, punctuation included.",
    },
    pitfalls: "bge-small cannot run in the browser, so on a bge set the box searches the chunk text instead.",
    links: [EMBEDDING, HASHING],
  },
  {
    id: "rag:selection-cosine",
    title: "Mean cosine of a selection",
    purpose:
      "The average cosine over every pair of selected points, in the full space, next to the same average over the whole cloud. A selection far above the cloud's average is a real tight group, not an artefact of the projection.",
    formula: "\\bar{c} = \\frac{2}{m(m-1)} \\sum_{a < b} \\cos(x_a, x_b)",
    interpretation: {
      tip: "Above 40,000 pairs the mean is estimated from 40,000 seeded random pairs, marked ≈.",
    },
    links: [ARTICLE],
  },
  {
    id: "rag:great",
    title: "GReaT serialization",
    purpose:
      "A row becomes a sentence an embedder can read: one “column is value” clause per column, joined by commas, in the schema's fixed order, with a missing value written “null”. The pipeline embeds these sentences and never shuffles the clauses, because a shuffled sentence would be a different vector for the same row.",
    formula: "c_1 \\text{ is } v_1,\\ c_2 \\text{ is } v_2,\\ \\dots,\\ c_m \\text{ is } v_m",
    interpretation: {
      tip: "The column name travels with the value: amount is 4.5 and shirt is 4 are different sentences.",
    },
    pitfalls:
      "Values are not escaped: a comma or “is” inside a value is indistinguishable from the separator, exactly as in the pipeline.",
    diagram: "rag:great",
    links: [
      GREAT,
      {
        label: "Xu et al. 2024 — on column order in LLM table generation",
        url: "https://arxiv.org/abs/2406.14541",
        kind: "paper",
      },
      SERIALIZE,
    ],
  },
  {
    id: "rag:row-parse",
    title: "The row, read back from its sentence",
    purpose:
      "rag_chunks keeps the sentence, not the row (and never source_pk), so the lab splits the stored sentence at its “, column is ” boundaries, learned from the table's own row documents. Re-serialising the unedited clauses gives back the stored text byte for byte.",
    links: [SERIALIZE],
  },
  {
    id: "rag:hashing-embedder",
    title: "HashingEmbedder (hashing-384)",
    purpose:
      "The dependency-free embedder: every whitespace token is hashed (SHA-256 of seed, a NUL byte and the token) into one of 384 buckets with a sign, the signed counts are summed, then L2-normalised. It is lexical, not semantic: texts are near only when they share tokens.",
    formula:
      "v_j = \\frac{1}{Z}\\sum_{t\\,:\\,h(t) = j} s(t), \\qquad h(t) = \\operatorname{uint64}\\big(\\operatorname{SHA256}(0 \\,\\Vert\\, t)_{0..7}\\big) \\bmod 384",
    interpretation: {
      tip: "Tokens come from Python's str.split(): punctuation stays attached, so “Pine,” and “Pine” are different tokens.",
    },
    pitfalls:
      "Two tokens can share a bucket (a collision) and cancel or add; with about 20 tokens and 384 buckets a collision or two per row is expected.",
    diagram: "rag:hashing",
    links: [EMBEDDING, HASHING],
  },
  {
    id: "rag:hashing-noise",
    title: "Hashing noise: ±1/√384",
    purpose:
      "Two unrelated texts hashed into 384 signed buckets land at a cosine near 0 with a spread of about 1/√384 ≈ 0.051. A cosine inside that band means nothing; one well above it means shared tokens.",
    formula:
      "\\cos(u, v) \\approx \\mathcal{N}\\!\\left(0, \\tfrac{1}{d}\\right) \\;\\Rightarrow\\; \\pm\\frac{1}{\\sqrt{384}} \\approx \\pm 0.051",
    interpretation: {
      tip: "Row documents of one table share every column name and every “is”, so their cosines start far above the band: the schema sets the floor.",
    },
    diagram: "rag:noise-band",
    links: [HASHING, EMBEDDING],
  },
  {
    id: "rag:bge",
    title: "bge-small-en-v1.5",
    purpose:
      "The production embedder: 384 dimensions, 33.4M parameters, inputs cut at 512 tokens, MIT-licensed, loaded from GCS with the Hub offline. Nearness means meaning rather than shared tokens.",
    interpretation: {
      tip: "The engine mean-pools the last hidden state over the attention mask; the model card pools on [CLS] (see the Docs differ note).",
    },
    pitfalls:
      "Whether [CLS] pooling would pick better seeds here is unmeasured. A 200-column row can exceed 512 tokens and is truncated.",
    links: [BGE_CARD, CPACK, EMBEDDING],
  },
  {
    id: "rag:faiss-flatip",
    title: "FAISS IndexFlatIP",
    purpose:
      "Not an index in the database sense: the vectors stacked as an n × 384 float32 matrix, where a search is one matrix–vector product and a top-k. On L2-normalised vectors the inner product is the cosine, so the ranking is exact.",
    formula:
      "\\operatorname{search}(q) = \\operatorname{top}_k(Xq), \\qquad \\lVert x_i \\rVert = 1 \\Rightarrow x_i \\cdot q = \\lVert q\\rVert\\cos\\theta_i",
    interpretation: {
      good: "Exact, deterministic (single-threaded), nothing to train or tune.",
      tip: "FAISS's own guidance: for a few searches, direct computation beats building any index.",
    },
    diagram: "rag:unit-sphere",
    links: [FAISS, FAISS_WIKI, INDEX],
  },
  {
    id: "rag:faiss-memory",
    title: "Index memory",
    purpose:
      "IndexFlatIP stores nothing but the vectors: n × d × 4 bytes. At the 1,024-row cap and 384 dimensions that is 1,572,864 bytes, 1.5 MiB, which fits in a CPU cache.",
    formula: "n \\cdot d \\cdot 4\\,\\mathrm{B} = 1024 \\cdot 384 \\cdot 4 = 1{,}572{,}864\\ \\mathrm{B}",
    links: [INDEX, FAISS_WIKI],
  },
  {
    id: "rag:index-lifecycle",
    title: "Rebuilt per engine build, never persisted",
    purpose:
      "The vectors are persisted in rag_chunks, keyed by digest and embedder; the index is rebuilt in RAM by every engine build and released after its handful of questions. Reading the vectors back costs seconds; stacking them, a fraction of a second.",
    interpretation: {
      tip: "A partial or dimension-mismatched read is discarded (all-or-nothing): mixing vector spaces is worse than re-embedding.",
    },
    links: [INDEX, ENGINE, ARTICLE],
  },
  {
    id: "rag:row-doc-cap",
    title: "MAX_ROW_DOC_ROWS = 1,024",
    purpose:
      "Only the first 1,024 rows of the fingerprint-ordered reference sample become row documents, because the only reader of row vectors reads exactly those, all-or-nothing. One constant is shared by the writer and the reader.",
    interpretation: {
      tip: "The prefix of a fingerprint-ordered sample is a uniform sample: right for a centroid, weak for a rare mode.",
    },
    pitfalls:
      "Rows past 1,024 are not row documents, but their free-text values are still value chunks (distinct, up to 1,024 per column).",
    links: [
      CHUNKING,
      adr("ADR 0019 — population scoped to its consumers", "0019-rag-population-scoped-to-consumers.md"),
    ],
  },
  {
    id: "rag:seed-strategy",
    title: "Seed strategy (--pool_seed_strategy)",
    purpose:
      "Which eight real values of a column the pool prompt shows the LLM: centroid (the default), kcenter or kcenter_rotate. The choice happens once per column per engine build, never per row.",
    interpretation: {
      tip: "Typicality gives the model one unambiguous format; coverage shows it the column's variety.",
    },
    links: [RETRIEVAL, TABGEN, ARTICLE],
  },
  {
    id: "rag:centroid-topk",
    title: "Centroid top-k",
    purpose:
      "The k values nearest the mean of all the column's vectors: the most typical ones. The default strategy and the only one with Dataflow evidence.",
    formula: "c = \\frac{1}{n}\\sum_i x_i, \\qquad S = \\operatorname{top}_k\\ \\cos(x_i, c)",
    interpretation: {
      good: "One dominant format, shown eight times: a clear instruction.",
      bad: "A sparse column whose rare formats never reach the prompt.",
    },
    pitfalls:
      "Production asks FAISS in float32; scores closer than ~1e-7 can order differently from this float64 port.",
    diagram: "rag:centroid",
    links: [RETRIEVAL, FAISS],
  },
  {
    id: "rag:kcenter",
    title: "k-center (farthest point)",
    purpose:
      "Greedy farthest-point traversal: start at the medoid, then repeatedly add the value farthest from every value picked so far. It maximises coverage of the column's space and needs distances, not an index.",
    formula:
      "s_1 = \\arg\\min_i \\lVert x_i - \\bar{x} \\rVert^2, \\qquad s_{t+1} = \\arg\\max_i \\min_{j \\le t} \\lVert x_i - s_j \\rVert^2",
    interpretation: {
      good: "Every mode of the column reached within a few picks.",
      tip: "A 2-approximation of the optimal k-center radius (Gonzalez 1985); ties go to the lower index.",
    },
    pitfalls: "It seeks outliers by design: one malformed value far from the rest will be picked.",
    diagram: "rag:kcenter",
    links: [GONZALEZ, SENER, RETRIEVAL],
  },
  {
    id: "rag:kcenter-rotate",
    title: "k-center rotate",
    purpose:
      "The same farthest-point walk, restarted at item (attempt × k) mod n on each ladder round, so every round shows the model another region. The prompt changes from its Examples line on, forfeiting the prefix cache past the instruction by design.",
    formula:
      "s_1^{(a)} = x_{(a \\cdot k) \\bmod n}, \\qquad s_{t+1} = \\arg\\max_i \\min_{j \\le t} \\lVert x_i - s_j \\rVert^2",
    pitfalls:
      "The start is an index, so it depends on item order: the sample's on the pool branch, the chunk store's read (no ORDER BY) on Generate.",
    diagram: "rag:kcenter",
    links: [
      GONZALEZ,
      RETRIEVAL,
      {
        label: "vLLM automatic prefix caching",
        url: "https://docs.vllm.ai/en/stable/design/prefix_caching/",
        kind: "docs",
      },
    ],
  },
  {
    id: "rag:mmr",
    title: "MMR — teaching contrast, not in pipeline",
    purpose:
      "Maximal Marginal Relevance trades relevance to a query (here, the centroid) against similarity to the picks so far. Shown only to contrast: it still orbits its query, and the pipeline never runs it.",
    formula:
      "\\arg\\max_{i \\notin S}\\ \\lambda\\cos(x_i, c) - (1 - \\lambda)\\max_{j \\in S}\\cos(x_i, x_j), \\qquad \\lambda = 0.5",
    links: [MMR],
  },
  {
    id: "rag:random-seeds",
    title: "Random — teaching contrast, not in pipeline",
    purpose:
      "k values drawn uniformly at random (one seeded draw). Shown only to contrast: random follows the mass, like taking the first k values, and randomly chosen examples hamper in-context generation.",
    links: [TABGEN],
  },
  {
    id: "rag:coverage",
    title: "Coverage",
    purpose:
      "How close every item sits to its nearest seed, on average, in cosine distance: lower means the seeds stand for the whole column. The worst-covered item's distance is the k-center objective.",
    formula: "\\operatorname{cov}(S) = \\frac{1}{n}\\sum_{i=1}^{n} \\min_{s \\in S}\\big(1 - \\cos(x_i, s)\\big)",
    links: [GONZALEZ],
  },
  {
    id: "rag:diversity",
    title: "Diversity",
    purpose: "The average cosine distance between two seeds: higher means the prompt shows more varied examples.",
    formula: "\\operatorname{div}(S) = \\frac{2}{k(k-1)}\\sum_{a < b}\\big(1 - \\cos(s_a, s_b)\\big)",
    links: [MMR],
  },
  {
    id: "rag:redundancy",
    title: "Redundancy",
    purpose:
      "For each seed, the cosine to its closest fellow seed, averaged: 1 means near-duplicates in the prompt, spending examples (and leak surface) on the same thing twice.",
    formula: "\\operatorname{red}(S) = \\frac{1}{k}\\sum_{a} \\max_{b \\ne a}\\cos(s_a, s_b)",
    links: [ARTICLE],
  },
  {
    id: "rag:seed-ladder",
    title: "Where seed candidates come from",
    purpose:
      "A fixed fallback order: persisted value vectors, then the column's values in the first 1,024 rows embedded locally, then the row exemplars, then the first observed examples. Which rung fires depends on which DoFn ladders: the pool branch has no chunk store, so it uses rung 2.",
    interpretation: {
      tip: "Rung 3 cannot fire as the engine is wired: the same 1,024-row prefix already fed rung 2.",
    },
    diagram: "rag:seed-ladder",
    links: [ENGINE, ARTICLE],
  },
  {
    id: "rag:pool-target",
    title: "Pool target",
    purpose:
      "How many distinct values the ladder aims for: the smallest of the rows to generate, the column's distinct count (exact tier first, then the source filter, then the sample) and the 512 cap. The target is recorded in the pool row, not keyed, so a pool built for a small run serves a large one.",
    formula: "\\text{target} = \\max\\!\\big(1,\\ \\min(\\text{num\\_rows},\\ \\text{distinct},\\ 512)\\big)",
    diagram: "rag:pool-target",
    links: [
      ENGINE,
      adr("ADR 0020 — free-text pools as a persisted artifact", "0020-freetext-pools-as-persisted-artifact.md"),
    ],
  },
  {
    id: "rag:pool-stagnation",
    title: "Stagnation",
    purpose:
      "The ladder stops early when, after every sampling level has run once, three attempts in a row each add fewer than four novel values. The pool row records it, so a later worker reads the conclusion instead of re-running the ladder.",
    interpretation: {
      bad: "A stagnated pool is smaller than its target: fewer distinct values, more repeats per value at scale.",
    },
    links: [ENGINE, adr("ADR 0018 — parallel, batched free-text pools", "0018-parallel-batched-freetext-pools.md")],
  },
  {
    id: "rag:call-budget",
    title: "Ladder call budget",
    purpose:
      "Each attempt asks for four completions of 32 values; the ladder stops at the target, at stagnation, after two format-collapsed rounds, or at its call budget. The table records attempts, not a per-round history.",
    formula:
      "\\text{calls} \\le \\max\\!\\Big(3,\\ 2\\Big\\lceil \\frac{\\text{target}}{32 \\cdot 4} \\Big\\rceil\\Big), \\qquad \\text{ideal} = \\Big\\lceil \\frac{\\text{target}}{128} \\Big\\rceil",
    links: [ENGINE],
  },
  {
    id: "rag:pool-routes",
    title: "Pool route (inferred)",
    purpose:
      "A pool row carries no route, so the tab infers it: LLM ladder when attempts > 0, binary fallback when most values carry control characters (the template pool records no attempts), no LLM rounds otherwise. A free-text column with value chunks but no pool row draws from shape-mix expansion or a clause sampler (Tier P/B).",
    pitfalls:
      "“No LLM rounds” on a text pool means the ladder delivered nothing and lax mode folded observed examples: check it for copies.",
    links: [
      ENGINE,
      code("engines/text_shapes.py — is_binary_class", "packages/sdfb-core/src/sdfb_core/engines/text_shapes.py"),
    ],
  },
  {
    id: "rag:pool-reuse",
    title: "Pool reuse at scale",
    purpose:
      "Rows draw a pooled column uniformly with replacement, so each value repeats about rows ÷ pool size times. The tail's source frequencies are not reproduced; the cap trades variety for LLM cost.",
    formula: "\\text{repeats} \\approx \\frac{\\text{rows}}{\\lvert \\text{pool} \\rvert}",
    interpretation: {
      tip: "The cap is free_text_pool_max (a code constant); see the CONFIG tab for what raising it would cost.",
    },
    links: [ENGINE, ARTICLE],
  },
]);
