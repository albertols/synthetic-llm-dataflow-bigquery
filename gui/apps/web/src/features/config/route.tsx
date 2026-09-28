/**
 * CONFIG route module (owned by the CONFIG tab agent). Contract: `searchSchema`
 * (`zod/mini`, it sits in the shell chunk) + lazy `component`; the page reads
 * params with `getRouteApi("/config").useSearch()`.
 *
 *   section   amp | scenario | sources | guardrails (in-page tab)
 *   knob      knobs.json id whose sheet is open
 *   scenario  calculator preset id (e.g. "90m-from-1m")
 *   table     source-stats table (fqn or bare name)
 *   digest    reference digest to compare across tiers
 *   snapshot  one snapshot key (digest|tier|profiler_version|run_id)
 *   column    source-stats column whose detail is open
 */
import { lazyRouteComponent } from "@tanstack/react-router";
import { z } from "zod/mini";

const optionalString = z.catch(z.optional(z.string().check(z.maxLength(400))), undefined);

export const CONFIG_SECTIONS = ["amp", "scenario", "sources", "guardrails"] as const;
export type ConfigSection = (typeof CONFIG_SECTIONS)[number];

export const searchSchema = z.object({
  section: z.catch(z.optional(z.enum(CONFIG_SECTIONS)), undefined),
  /** Knob id whose sheet is open (a knobs.json id). */
  knob: optionalString,
  /** Scenario preset id for the calculator. */
  scenario: optionalString,
  table: optionalString,
  digest: optionalString,
  snapshot: optionalString,
  column: optionalString,
});
export type ConfigSearch = z.infer<typeof searchSchema>;

export const component = lazyRouteComponent(() => import("./ConfigPage"), "ConfigPage");
