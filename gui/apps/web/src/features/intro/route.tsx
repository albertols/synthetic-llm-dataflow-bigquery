/**
 * INTRO route module (owned by the INTRO tab agent).
 *
 * Contract with src/router.tsx: export `searchSchema` (validated by the
 * router before render) and `component` (lazy: the page code stays out of
 * the shell chunk). Inside the page read params with
 * `getRouteApi("/").useSearch()`.
 *
 * Search params: the glossary's query `q` and concept group `ns`, so a search
 * is shareable. Malformed values fall back to "none".
 *
 * Search params are validated with `@/lib/search` (plain checks, no zod:
 * route modules sit in the shell chunk).
 */
import { lazyRouteComponent } from "@tanstack/react-router";

import { searchParams, text, type SearchOf } from "@/lib/search";

export const searchSchema = searchParams({
  /** Glossary query. */
  q: text({ max: 120 }),
  /** Glossary concept group: a concept id namespace ("metric", "intro" …). */
  ns: text({ pattern: /^[a-z][a-z0-9_-]{0,23}$/ }),
});
export type IntroSearch = SearchOf<typeof searchSchema>;

export const component = lazyRouteComponent(() => import("./IntroPage"), "IntroPage");
