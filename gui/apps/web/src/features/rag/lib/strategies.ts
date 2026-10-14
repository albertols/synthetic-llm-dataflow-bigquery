/**
 * The five seed pickers the RAG tab shows, with their truth labels.
 *
 * - centroid, kcenter, kcenter_rotate: `--pool_seed_strategy` values. They
 *   run through `selectSeedExamples` from @synthetic-platform/stats, an exact
 *   port of `sdfb_core.rag.retrieval.select_seed_examples` pinned by the
 *   retrieval golden (see retrieval.golden.test.ts in this folder).
 * - random, mmr: teaching contrasts. Neither runs anywhere in the pipeline.
 *
 * Production runs the centroid query on FAISS `IndexFlatIP` in float32; the
 * port computes in float64 over the same (float32) vectors, so two scores
 * within ~1e-7 of each other can order differently there. k-center needs no
 * index at all.
 */
import {
  kcenterWalk,
  medoidIndex,
  mmr,
  randomPick,
  selectSeedExamples,
  type KcenterStep,
} from "@synthetic-platform/stats";

import type { Flat } from "./vectors";

export const STRATEGY_IDS = ["centroid", "kcenter", "kcenter_rotate", "random", "mmr"] as const;
export type StrategyId = (typeof STRATEGY_IDS)[number];
export const PIPELINE_STRATEGIES = ["centroid", "kcenter", "kcenter_rotate"] as const;

export interface StrategyInfo {
  id: StrategyId;
  label: string;
  /** Short description of what it optimises. */
  picks: string;
  inPipeline: boolean;
  /** "Exact port" or "Teaching contrast — not in pipeline". */
  truth: string;
  /** The launcher flag, for pipeline strategies. */
  flag?: string;
  /** Concept id for the (i). */
  concept: string;
  /** What is known about it from runs. */
  evidence: string;
}

export const TEACHING_LABEL = "Teaching contrast — not in pipeline";
export const EXACT_LABEL = "Exact port of the pipeline";

export const STRATEGIES: Record<StrategyId, StrategyInfo> = {
  centroid: {
    id: "centroid",
    label: "Centroid top-k",
    picks: "the k items nearest the mean vector (typicality)",
    inPipeline: true,
    truth: EXACT_LABEL,
    flag: "--pool_seed_strategy=centroid",
    concept: "rag:centroid-topk",
    evidence: "Default, and the only arm with Dataflow evidence.",
  },
  kcenter: {
    id: "kcenter",
    label: "k-center",
    picks: "the medoid, then the item farthest from every earlier pick (coverage)",
    inPipeline: true,
    truth: EXACT_LABEL,
    flag: "--pool_seed_strategy=kcenter",
    concept: "rag:kcenter",
    evidence: "Implemented and unit-tested; the A/B has not run yet.",
  },
  kcenter_rotate: {
    id: "kcenter_rotate",
    label: "k-center rotate",
    picks: "the same walk, restarted at item (attempt × k) mod n each ladder round",
    inPipeline: true,
    truth: EXACT_LABEL,
    flag: "--pool_seed_strategy=kcenter_rotate",
    concept: "rag:kcenter-rotate",
    evidence: "Implemented and unit-tested; the A/B has not run yet. Forfeits the prefix cache past the instruction.",
  },
  random: {
    id: "random",
    label: "Random",
    picks: "k items uniformly at random (one seeded draw)",
    inPipeline: false,
    truth: TEACHING_LABEL,
    concept: "rag:random-seeds",
    evidence: "Never runs in the pipeline: random follows the mass.",
  },
  mmr: {
    id: "mmr",
    label: "MMR (λ = 0.5)",
    picks: "relevance to the centroid traded against similarity to earlier picks",
    inPipeline: false,
    truth: TEACHING_LABEL,
    concept: "rag:mmr",
    evidence: "Never runs in the pipeline: the classic diversity re-ranker it is not.",
  },
};

export function isStrategyId(value: unknown): value is StrategyId {
  return typeof value === "string" && (STRATEGY_IDS as readonly string[]).includes(value);
}

export interface StrategyOptions {
  /** kcenter_rotate: the ladder attempt (round). */
  attempt?: number;
  /** random: the seed of the one draw shown. */
  seed?: number;
  /** mmr: relevance weight (1 = pure relevance). */
  lambda?: number;
}

export const TEACHING_SEED = 7;
export const MMR_LAMBDA = 0.5;

/**
 * The indices a strategy picks from `rows`. Pipeline strategies go through
 * `selectSeedExamples` exactly as `select_seed_examples` dispatches them
 * (n ≤ k returns every item, in order).
 */
export function runStrategy(id: StrategyId, rows: readonly Flat[], k: number, options: StrategyOptions = {}): number[] {
  if (!rows.length || k <= 0) return [];
  switch (id) {
    case "centroid":
    case "kcenter":
    case "kcenter_rotate":
      return selectSeedExamples(rows, k, id, options.attempt ?? 0);
    case "random":
      return randomPick(rows.length, k, options.seed ?? TEACHING_SEED);
    case "mmr":
      return mmr(rows, k, options.lambda ?? MMR_LAMBDA);
  }
}

/** Where the k-center walk starts: the medoid, or (attempt × k) mod n for kcenter_rotate. */
export function walkStart(id: StrategyId, n: number, k: number, attempt: number, rows: readonly Flat[]): number {
  if (id === "kcenter_rotate") return n > 0 && k > 0 ? (attempt * k) % n : 0;
  return rows.length ? medoidIndex(rows) : 0;
}

/** The greedy farthest-point walk for the animation: the same picks as `kcenter` / `kcenter_rotate`. */
export function strategyWalk(id: StrategyId, rows: readonly Flat[], k: number, attempt = 0): KcenterStep[] {
  if (!rows.length || k <= 0) return [];
  const n = rows.length;
  if (id === "kcenter_rotate") return kcenterWalk(rows, k, k > 0 ? (attempt * k) % n : 0);
  return kcenterWalk(rows, k);
}
