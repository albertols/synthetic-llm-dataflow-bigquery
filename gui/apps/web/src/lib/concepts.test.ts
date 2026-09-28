import katex from "katex";
import { beforeAll, describe, expect, it } from "vitest";

import { conceptSchema } from "@contracts/concept.schema";

import { hasDiagram } from "@/components/MiniDiagram";

import { conceptProblems, conceptsComplete, getConcept, listConcepts, loadConcepts } from "./concepts";

/** The contract every concept file must meet (tab files included — this suite is their gate). */
describe("concept registry", () => {
  beforeAll(async () => {
    await loadConcepts();
  });

  it("loads every concept file without duplicate ids or malformed modules", () => {
    expect(conceptsComplete()).toBe(true);
    expect(conceptProblems).toEqual([]);
  });

  it("carries the foundation vocabulary", () => {
    for (const id of ["core:noise-floor", "core:baseline", "core:score", "core:status", "core:data-source"]) {
      expect(getConcept(id), id).toBeDefined();
    }
    for (const level of ["field", "column", "pair", "row", "table", "relationship", "model"]) {
      expect(getConcept(`core:level-${level}`)?.level).toBe(level);
    }
  });

  it("every concept parses, its formula renders in KaTeX, and its diagram exists", () => {
    const failures: string[] = [];
    for (const concept of listConcepts()) {
      const parsed = conceptSchema.safeParse(concept);
      if (!parsed.success)
        failures.push(
          `${concept.id}: ${parsed.error.issues.map((i) => `${i.path.join(".")} ${i.message}`).join("; ")}`,
        );
      if (concept.formula) {
        try {
          katex.renderToString(concept.formula, { throwOnError: true, strict: "ignore" });
        } catch (error) {
          failures.push(`${concept.id}: formula does not parse (${String(error)})`);
        }
      }
      if (concept.diagram && !hasDiagram(concept.diagram))
        failures.push(`${concept.id}: unknown diagram "${concept.diagram}"`);
      const sentences = concept.purpose.split(/(?<=[.!?])\s+(?=[A-Z0-9"“(])/).length;
      if (sentences > 2) failures.push(`${concept.id}: purpose has ${sentences} sentences (max 2)`);
    }
    expect(failures).toEqual([]);
  });
});
