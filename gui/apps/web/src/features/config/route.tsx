/**
 * CONFIG route module (owned by the CONFIG tab agent). Contract: `searchSchema`
 * (`@/lib/search`, it sits in the shell chunk) + lazy `component`; the page reads
 * params with `getRouteApi("/config").useSearch()`.
 *
 *   section   amp | scenario | sources | guardrails (in-page tab)
 *   channel   knobs.json channel the amp shows first (e.g. "free_text")
 *   knob      knobs.json id whose sheet is open
 *   scenario  calculator preset id (e.g. "90m-from-1m")
 *   table     source-stats table (fqn or bare name)
 *   digest    reference digest to compare across tiers
 *   snapshot  one snapshot key (digest|tier|profiler_version|run_id)
 *   column    source-stats column whose detail is open
 *   N M n p q tier uniq env K D s sdk warm filter
 *             scenario inputs that differ from the preset (model/state.tsx),
 *             so a deep link reproduces the calculator
 */
import { lazyRouteComponent } from "@tanstack/react-router";

import { finite, flag, oneOf, searchParams, text, type SearchOf } from "@/lib/search";

const optionalString = text({ max: 400 });
const optionalNumber = finite();
const optionalBoolean = flag();

export const CONFIG_SECTIONS = ["amp", "scenario", "sources", "guardrails"] as const;
export type ConfigSection = (typeof CONFIG_SECTIONS)[number];

export const searchSchema = searchParams({
  section: oneOf(CONFIG_SECTIONS),
  /** The amp channel shown (a knobs.json channel id; the amp ignores one it does not know). */
  channel: text({ max: 40, pattern: /^[a-z][a-z_]*$/ }),
  /** Knob id whose sheet is open (a knobs.json id). */
  knob: optionalString,
  /** Scenario preset id for the calculator. */
  scenario: optionalString,
  table: optionalString,
  digest: optionalString,
  snapshot: optionalString,
  column: optionalString,
  N: optionalNumber,
  M: optionalNumber,
  n: optionalNumber,
  p: optionalNumber,
  q: optionalNumber,
  tier: optionalString,
  uniq: optionalString,
  env: optionalString,
  K: optionalNumber,
  D: optionalNumber,
  s: optionalNumber,
  sdk: optionalString,
  warm: optionalBoolean,
  filter: optionalBoolean,
});
export type ConfigSearch = SearchOf<typeof searchSchema>;

export const component = lazyRouteComponent(() => import("./ConfigPage"), "ConfigPage");
