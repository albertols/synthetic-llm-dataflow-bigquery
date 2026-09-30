/** Runtime validation for the concept contract (tests and tooling; not bundled in the shell). */
import { z } from "zod";

import { conceptIdPattern, conceptLevels, conceptLinkKinds, type Concept } from "./concept";

export const conceptLinkSchema = z.object({
  label: z.string().min(1),
  url: z.url({ protocol: /^https$/ }),
  kind: z.enum(conceptLinkKinds),
});

export const conceptSchema = z.object({
  id: z.string().regex(conceptIdPattern),
  title: z.string().min(1),
  purpose: z.string().min(1),
  formula: z.string().min(1).optional(),
  interpretation: z
    .object({ good: z.string().optional(), bad: z.string().optional(), tip: z.string().optional() })
    .optional(),
  pitfalls: z.string().optional(),
  diagram: z.string().regex(conceptIdPattern).optional(),
  links: z.array(conceptLinkSchema),
  level: z.enum(conceptLevels).optional(),
}) satisfies z.ZodType<Concept>;
