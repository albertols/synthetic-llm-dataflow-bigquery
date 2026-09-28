/**
 * CONFIG route module (owned by the CONFIG tab agent). Contract: `searchSchema`
 * (`zod/mini`, see intro/route.tsx) + lazy `component`; read params with
 * `getRouteApi("/config").useSearch()`.
 */
import { lazyRouteComponent } from "@tanstack/react-router";
import { z } from "zod/mini";

export const searchSchema = z.object({
  /** Knob id whose sheet is open (a knobs.json id). */
  knob: z.catch(z.optional(z.string()), undefined),
  /** Scenario preset id for the calculator. */
  scenario: z.catch(z.optional(z.string()), undefined),
});
export type ConfigSearch = z.infer<typeof searchSchema>;

export const component = lazyRouteComponent(() => import("./ConfigPage"), "ConfigPage");
