/**
 * INTRO route module (owned by the INTRO tab agent).
 *
 * Contract with src/router.tsx: export `searchSchema` (zod, validated by the
 * router before render) and `component` (lazy: the page code stays out of
 * the shell chunk). Inside the page read params with
 * `getRouteApi("/").useSearch()`.
 *
 * Search params: the glossary's query `q` and concept group `ns`, so a search
 * is shareable. Malformed values fall back to "none" (`z.catch`).
 *
 * Search schemas use `zod/mini` (tree-shakable) because route modules sit in
 * the shell chunk; classic `zod` is fine inside the lazy page code.
 */
import { lazyRouteComponent } from "@tanstack/react-router";
import { z } from "zod/mini";

export const searchSchema = z.object({
  /** Glossary query. */
  q: z.catch(z.optional(z.string().check(z.maxLength(120))), undefined),
  /** Glossary concept group: a concept id namespace ("metric", "intro" …). */
  ns: z.catch(z.optional(z.string().check(z.regex(/^[a-z][a-z0-9_-]{0,23}$/))), undefined),
});
export type IntroSearch = z.infer<typeof searchSchema>;

export const component = lazyRouteComponent(() => import("./IntroPage"), "IntroPage");
