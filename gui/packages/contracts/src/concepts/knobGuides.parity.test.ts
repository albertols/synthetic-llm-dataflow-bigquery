import { describe, expect, it } from "vitest";

import master from "./__parity__/knob-guides.master.json";
import { knobGuides } from "./config";

/** The tip the catalogue gives a constant that a config file can now set (A1 plan Task 3). */
const PROMOTED_TIP = "Set it in a config file (--config_uri); a cache it shapes is rebuilt under the new value.";
/** The tip those constants carried on master. */
const MASTER_TIP = "A constant: changing it is a code edit, a test run and an image rebuild.";

type ProseField = "purpose" | "up" | "down";

/**
 * Prose rewritten since master because it promised effects the code does not have: both keys
 * change only the source_table_stats rows (A1 final review F4). Exactly these ids and fields;
 * each reads as master's in the snapshot only while it equals the text here.
 */
const REWRITTEN_PROSE: Record<string, Partial<Record<ProseField, string>>> = {
  shape_mix_top_k: {
    purpose:
      "How many character-shape masks (9 digit, A upper, a lower) a column's source_table_stats entry " +
      "keeps, with their shares. The engines build their own shape mixes and never read it.",
    up: "The stats entry records more of a long-tailed format's minority masks; generation does not change.",
    down: "The stats entry keeps only the dominant masks; generation does not change.",
  },
  length_percentiles: {
    purpose:
      "The three length quantiles a string column's source_table_stats entry records, under the fixed " +
      "names len_p05, len_p50 and len_p95. The pool prompt's length hint has its own fixed p05–p95 band " +
      "and never reads these.",
    up: "A wider band records more of the length tails in the stats entry; prompts and generation do not change.",
    down: "A tighter band records only typical lengths in the stats entry; prompts and generation do not change.",
  },
  // The deliberate changes from master (config_parity.DELIBERATE_CHANGES, A1 plan Task 3): both
  // build layers default to true, a no-op without their table; gpu and sdk_containers are settings
  // now, no longer Composer-only parameters, and sdk_containers defaults to multi.
  build_pool_layer: {
    up: "true: pools are built once and every worker reads them (warm runs skip the LLM); a no-op without freetext_pools_table.",
  },
  build_rag_layer: {
    up: "true: chunks and vectors persist, and later runs read them instead of re-embedding; a no-op without rag_chunks_table.",
  },
  gpu: {
    purpose: "The workers' GPU; a null vllm_dtype follows it.",
    up: "l4: bfloat16, more VRAM.",
    down: "t4: cheaper; float16 only, no Gemma.",
  },
  sdk_containers: {
    purpose: "SDK processes per worker.",
    up: "multi: one per vCPU, past the GIL ceiling.",
    down: "single: one per worker.",
  },
};

/**
 * Tips changed by the same deliberate changes: master's "The Composer DAG defaults it to …" no
 * longer holds once the generator's default is the DAG's (the build layers) or derived from the
 * GPU (vllm_dtype), and the two serving keys gained a tip. id → the tip now (undefined: none).
 * Each reads as master's in the snapshot only while it equals the text here.
 */
const REWRITTEN_TIPS: Record<string, string | undefined> = {
  build_pool_layer: undefined,
  build_rag_layer: undefined,
  vllm_dtype: undefined,
  gpu:
    "The Composer DAG picks the worker machine and accelerator from it (l4 or t4); the launcher derives a " +
    "null vllm_dtype from it (t4 → float16, l4 → bfloat16) and records it. Gemma cannot run on a T4, and " +
    "models too large for its VRAM fit only an L4.",
  sdk_containers:
    "The worker topology of a vLLM launch: the Composer DAG turns single into the " +
    "no_use_multiple_sdk_containers experiment. multi lets generation escape the one-interpreter GIL " +
    "ceiling (the sheet charts the measured pair); with single one process owns the GPU. A direct launch " +
    "passes its experiments itself, and the launcher only records the value.",
};

/** A rewritten tip reads as master's while it equals the allowlisted text. */
function tipAsOnMaster(id: string, tip: string | undefined): string | undefined {
  if (!(id in REWRITTEN_TIPS) || tip !== REWRITTEN_TIPS[id]) return tip;
  return (master as Record<string, { tip?: string }>)[id]?.tip;
}

/** A rewritten field reads as master's while it equals the allowlisted text. */
function asOnMaster(id: string, field: ProseField, text: string): string {
  const rewritten = REWRITTEN_PROSE[id]?.[field];
  const onMaster = (master as Record<string, Partial<Record<ProseField, string>>>)[id]?.[field];
  return rewritten !== undefined && text === rewritten && onMaster !== undefined ? onMaster : text;
}

/**
 * Commit-free view of the guides master had, in the snapshot's order: code/doc links
 * lose the exported sha, and a promoted constant's tip reads as it did on master.
 * The knobs that are new since master are new prose, checked by the web knob tests.
 */
function serialise(): unknown {
  const out: Record<string, unknown> = {};
  for (const id of Object.keys(master)) {
    const guide = knobGuides[id as keyof typeof knobGuides];
    out[id] = {
      ...guide,
      purpose: asOnMaster(id, "purpose", guide.purpose),
      up: asOnMaster(id, "up", guide.up),
      down: asOnMaster(id, "down", guide.down),
      tip: guide.tip === PROMOTED_TIP ? MASTER_TIP : tipAsOnMaster(id, guide.tip),
      refs: guide.refs?.map((r) => ({ ...r, url: r.url.replace(/\/blob\/[0-9a-f]{40}\//, "/blob/<ref>/") })),
    };
  }
  return out;
}

describe("knob guides", () => {
  it("keep the prose they had on master (A1 plan Task 1/7)", async () => {
    await expect(JSON.stringify(serialise(), null, 2) + "\n").toMatchFileSnapshot(
      "./__parity__/knob-guides.master.json",
    );
  });

  it("promotes exactly 18 constant tips", () => {
    expect(Object.values(knobGuides).filter((g) => g.tip === PROMOTED_TIP)).toHaveLength(18);
  });

  it("rewrites only the prose the allowlist names, and all of it (A1 final review F4)", () => {
    for (const [id, fields] of Object.entries(REWRITTEN_PROSE)) {
      const guide = knobGuides[id as keyof typeof knobGuides];
      for (const [field, text] of Object.entries(fields)) {
        expect(guide[field as ProseField]).toBe(text);
      }
    }
    for (const [id, tip] of Object.entries(REWRITTEN_TIPS)) {
      expect((knobGuides[id as keyof typeof knobGuides] as { tip?: string }).tip, id).toBe(tip);
    }
  });
});
