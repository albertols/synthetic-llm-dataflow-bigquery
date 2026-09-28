import { describe, expect, it } from "vitest";

import type { Concept } from "@contracts/concept";

import { fold, indexConcepts, namespaceOf, searchConcepts } from "./glossary";

const concept = (id: string, title: string, purpose: string): Concept => ({ id, title, purpose, links: [] });

const CONCEPTS = [
  concept("core:noise-floor", "Noise floor", "The smallest metric value that sampling noise alone produces."),
  concept("intro:reference-sample", "Reference sample and the DKW band", "The bounded sample every distribution uses."),
  concept("metric:column.ks", "Kolmogorov–Smirnov distance", "The largest gap between two CDFs; see the noise floor."),
  concept("intro:role-driving", "Driving edge", "The one edge a child is generated from."),
];
const index = indexConcepts(CONCEPTS);

describe("glossary search", () => {
  it("folds case, accents and id punctuation", () => {
    expect(fold("Cramér's V — metric:pair.cramers_v")).toBe("cramer's v — metric pair cramers v");
  });

  it("ranks a title match above a mention in another concept's text", () => {
    expect(searchConcepts(index, "noise").map((c) => c.id)).toEqual(["core:noise-floor", "metric:column.ks"]);
  });

  it("needs every term to match, anywhere", () => {
    expect(searchConcepts(index, "dkw sample").map((c) => c.id)).toEqual(["intro:reference-sample"]);
    expect(searchConcepts(index, "dkw driving")).toEqual([]);
  });

  it("matches ids and narrows to one namespace", () => {
    expect(searchConcepts(index, "column.ks").map((c) => c.id)).toEqual(["metric:column.ks"]);
    expect(searchConcepts(index, "", "intro").map(namespaceOf)).toEqual(["intro", "intro"]);
    expect(searchConcepts(index, "smirnov").map((c) => c.id)).toEqual(["metric:column.ks"]);
  });

  it("lists everything by title for an empty query", () => {
    expect(searchConcepts(index, "  ").map((c) => c.title)).toEqual([
      "Driving edge",
      "Kolmogorov–Smirnov distance",
      "Noise floor",
      "Reference sample and the DKW band",
    ]);
  });
});
