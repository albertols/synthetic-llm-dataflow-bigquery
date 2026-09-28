/**
 * EVALUATION route module (owned by the EVALUATION tab agent).
 *
 * Three routes share it: "/evaluation" (list), "/evaluation/$evaluationId"
 * (run view) and "/evaluation/compare" (compare view). Each gets a zod search
 * schema and a lazy component; src/router.tsx wires them. Schemas use
 * `zod/mini` so the shell chunk stays small (see intro/route.tsx).
 */
import { lazyRouteComponent } from "@tanstack/react-router";
import { z } from "zod/mini";

/** List filters. The tab owns and extends this schema (tables, engine, model, date range …). */
export const listSearchSchema = z.object({
  q: z.catch(z.optional(z.string()), undefined),
});
export type EvaluationListSearch = z.infer<typeof listSearchSchema>;

/** Compare view: the evaluation ids side by side, plus the list filters. */
export const compareSearchSchema = z.extend(listSearchSchema, {
  ids: z.catch(z._default(z.array(z.string()).check(z.maxLength(12)), []), []),
});
export type EvaluationCompareSearch = z.infer<typeof compareSearchSchema>;

/** Run view: which column drawer is open, if any. */
export const runSearchSchema = z.object({
  column: z.catch(z.optional(z.string()), undefined),
});
export type EvaluationRunSearch = z.infer<typeof runSearchSchema>;

export const listComponent = lazyRouteComponent(() => import("./EvaluationPages"), "EvaluationListPage");
export const runComponent = lazyRouteComponent(() => import("./EvaluationPages"), "EvaluationRunPage");
export const compareComponent = lazyRouteComponent(() => import("./EvaluationPages"), "EvaluationComparePage");
