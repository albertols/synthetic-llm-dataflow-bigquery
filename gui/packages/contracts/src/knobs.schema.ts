/** Runtime validation of `generated/knobs.json` (tests and the BFF; kept out of the web shell). */
import { z } from "zod";

import type { KnobsFile, KnobValue } from "./knobs";

export const knobValueSchema: z.ZodType<KnobValue> = z.lazy(() =>
  z.union([
    z.string(),
    z.number(),
    z.boolean(),
    z.null(),
    z.array(knobValueSchema),
    z.record(z.string(), knobValueSchema),
  ]),
);

const sourceSchema = z.string().regex(/^[\w./-]+:\d+$/);

export const knobSchema = z
  .object({
    id: z.string().regex(/^[a-z][a-z0-9_]*$/),
    channel: z.string(),
    group: z.string().min(1),
    label: z.string().min(1),
    value: knobValueSchema,
    unit: z.string().nullable(),
    settable_via: z.array(z.enum(["cli", "composer", "flex", "config", "constant", "derived"])).min(1),
    cli_flag: z.string().startsWith("--").optional(),
    composer_param: z.string().optional(),
    composer_default: z.string().optional(),
    flex_param: z.string().optional(),
    config_key: z
      .string()
      .regex(/^[a-z_]+\.[a-z][a-z0-9_]*$/)
      .optional(),
    choices: z.array(knobValueSchema).optional(),
    required: z.boolean().optional(),
    help: z.string().optional(),
    comment: z.string().optional(),
    source: sourceSchema,
    source_token: z.string().min(1),
    related_adrs: z.array(z.string().regex(/^\d{4}$/)),
    docs: z.array(z.string()),
  })
  // A config file sets exactly the knobs that name the key it sets them with.
  .refine((k) => k.settable_via.includes("config") === (k.config_key !== undefined), {
    message: 'config_key is present exactly when settable_via has "config"',
  });

export const knobsFileSchema = z.object({
  generated_by: z.literal("scripts/gui/export_knobs.py"),
  note: z.string(),
  exported_from: z.object({
    commit: z
      .string()
      .regex(/^[0-9a-f]{40}$/)
      .nullable(),
    dirty: z.array(z.string()),
  }),
  channels: z.array(z.object({ id: z.string(), label: z.string(), description: z.string() })).min(1),
  knobs: z.array(knobSchema).min(1),
  annotations: z.array(
    z.object({
      id: z.string(),
      title: z.string(),
      knobs: z.array(z.string()),
      docs_say: z.string(),
      code_does: z.string(),
      evidence: z
        .array(z.object({ source: sourceSchema, role: z.enum(["docs", "code"]), excerpt: z.string().min(1) }))
        .min(1),
      links: z.array(z.object({ label: z.string(), url: z.url({ protocol: /^https$/ }) })).optional(),
      related_adrs: z.array(z.string()),
    }),
  ),
  measured_sources: z.array(z.object({ script: z.string(), provenance: z.string().min(1) })).min(1),
  measured: z.array(
    z.object({
      id: z.string(),
      script: z.string(),
      name: z.string(),
      value: knobValueSchema,
      block: z.string(),
      comment: z.string(),
      source: sourceSchema,
    }),
  ),
}) satisfies z.ZodType<KnobsFile>;
