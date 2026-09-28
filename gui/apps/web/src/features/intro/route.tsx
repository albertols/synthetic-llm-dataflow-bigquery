/**
 * INTRO route module (owned by the INTRO tab agent).
 *
 * Contract with src/router.tsx: export `searchSchema` (zod, validated by the
 * router before render) and `component` (lazy: the page code stays out of
 * the shell chunk). Inside the page read params with
 * `getRouteApi("/").useSearch()`.
 *
 * Search schemas use `zod/mini` (tree-shakable) because route modules sit in
 * the shell chunk; classic `zod` is fine inside the lazy page code.
 */
import { lazyRouteComponent } from "@tanstack/react-router";
import { z } from "zod/mini";

export const searchSchema = z.object({});
export type IntroSearch = z.infer<typeof searchSchema>;

export const component = lazyRouteComponent(() => import("./IntroPage"), "IntroPage");
