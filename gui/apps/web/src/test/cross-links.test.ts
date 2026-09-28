/**
 * Cross-tab links resolve: every INTRO stage and "How it works" card, every
 * EVALUATION related knob and the RAG pool badges point at a route that exists,
 * with search params the target route keeps (its validator does not drop them)
 * and knob / channel / section ids that knobs.json and the CONFIG page know.
 */
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { knobs } from "@contracts/generated/knobs";

import * as config from "@/features/config/route";
import { catalogueById } from "@contracts/generated/catalogue";
import { RELATED_KNOBS, REFERENCE_SAMPLE, relatedKnobs } from "@/features/evaluation/lib/relatedKnobs";
import { DESIGN_SECTIONS } from "@/features/intro/content/designSections";
import { STAGES } from "@/features/intro/content/stages";
import { configSearch, targetHref, type IntroTarget } from "@/features/intro/content/targets";
import { createAppRouter } from "@/router";

const router = createAppRouter(new QueryClient());
const KNOB_IDS = new Set<string>(knobs.knobs.map((k) => k.id));
const CHANNEL_IDS = new Set<string>(knobs.channels.map((c) => c.id));

/** The route a pathname lands on (a `$param` segment matches anything). */
function routeFor(pathname: string): string | undefined {
  return Object.keys(router.routesByPath).find((path) => {
    const pattern = new RegExp(`^${path.replace(/\$[^/]+/g, "[^/]+").replace(/\/$/, "")}/?$`);
    return pattern.test(pathname);
  });
}

function checkConfigSearch(search: Record<string, string>) {
  if (search.knob) expect(KNOB_IDS.has(search.knob), search.knob).toBe(true);
  if (search.channel) expect(CHANNEL_IDS.has(search.channel), search.channel).toBe(true);
  if (search.section) expect(config.CONFIG_SECTIONS as readonly string[]).toContain(search.section);
  // The CONFIG route keeps every param the link sends.
  expect(config.searchSchema(search as never)).toEqual(search);
}

function checkTarget(target: IntroTarget) {
  const url = new URL(targetHref(target), "http://localhost");
  expect(routeFor(url.pathname), url.pathname).toBeDefined();
  if (target.tab === "config") {
    const search = configSearch(target) as Record<string, string>;
    expect(Object.fromEntries(url.searchParams)).toEqual(search);
    checkConfigSearch(search);
  }
}

describe("cross-tab links resolve", () => {
  it("INTRO: each pipeline stage opens its tab, per the stage → tab map", () => {
    const facets = { latest: { evaluation_id: "eval-0040" } } as never;
    for (const stage of STAGES) checkTarget(stage.target({ facets }));
    const target = (id: string) => STAGES.find((s) => s.id === id)!.target({ facets: undefined });
    // retrieve/embed → RAG; profile/sample → CONFIG source stats; generate → the amp; evaluate → EVALUATION.
    expect(target("rag")).toEqual({ tab: "rag" });
    for (const id of ["source", "reference", "stats"])
      expect(target(id), id).toEqual({ tab: "config", section: "sources" });
    expect(target("generate")).toEqual({ tab: "config", section: "amp", channel: "generation" });
    expect(target("evaluate")).toEqual({ tab: "evaluation" });
  });

  it("INTRO: every 'How it works' card that links out lands on an existing route or knob", () => {
    const targets = DESIGN_SECTIONS.flatMap((s) => (s.explore ? [s.explore.target] : []));
    expect(targets.length).toBeGreaterThan(3);
    for (const target of targets) checkTarget(target);
  });

  it("EVALUATION: related knobs name catalogue metrics and knobs.json knobs; the link keeps its params", () => {
    for (const [metricId, related] of Object.entries(RELATED_KNOBS)) {
      expect(catalogueById, metricId).toHaveProperty([metricId]);
      for (const { knob, why } of related) {
        expect(KNOB_IDS.has(knob), `${metricId} → ${knob}`).toBe(true);
        expect(why.length).toBeGreaterThan(20);
        expect(routeFor("/config")).toBe("/config");
        checkConfigSearch({ section: "amp", knob });
      }
    }
    expect(KNOB_IDS.has(REFERENCE_SAMPLE.knob)).toBe(true);
    // The one rule: a metric with a stored baseline (metric(R, source)) links the reference sample size.
    for (const [id, meta] of Object.entries(catalogueById)) {
      const knobsFor = relatedKnobs(id).map((r) => r.knob);
      expect(knobsFor.includes("reference_rows_limit"), id).toBe(meta.baseline);
    }
    expect(relatedKnobs("column.distinct_ceiling_hit").map((r) => r.knob)).toEqual(["free_text_pool_max"]);
    expect(relatedKnobs("not.a.metric")).toEqual([]);
  });

  it("RAG: the pool route badges open CONFIG's FREE TEXT channel", () => {
    checkConfigSearch({ section: "amp", channel: "free_text" });
    expect(knobs.channels.find((c) => c.id === "free_text")?.label).toBe("FREE TEXT");
    // The pool knobs RAG talks about live on that channel.
    expect(knobs.knobs.find((k) => k.id === "free_text_pool_max")?.channel).toBe("free_text");
  });
});
