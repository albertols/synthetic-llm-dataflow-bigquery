import { describe, expect, it } from "vitest";

import { concepts, knobGuides } from "@contracts/concepts/config";
import { generatorGuides, type GeneratedGuide } from "@contracts/generated/knobGuides";
import { knobs as knobsFile, type KnobId } from "@contracts/generated/knobs";

import { settableText } from "../amp/KnobControl";

import { ADRS, CODE_REF, docTitle, sourceUrl } from "./links";
import {
  ANNOTATIONS,
  asText,
  choicesOf,
  dialSteps,
  formatKnobValue,
  KNOBS,
  kindOf,
  knob,
  MEASURED,
  positions,
  shortValue,
} from "./knobs";

describe("the knob model (knobs.json is the truth)", () => {
  it("every knob has a guide and a knob: concept; every guide is a knob", () => {
    const ids = new Set(KNOBS.map((k) => k.id));
    for (const id of ids) {
      expect(knobGuides[id as keyof typeof knobGuides], id).toBeDefined();
      expect(
        concepts.find((c) => c.id === `knob:${id}`),
        id,
      ).toBeDefined();
    }
    for (const id of Object.keys(knobGuides)) expect(ids.has(id), id).toBe(true);
    // 98 generator knobs (the setting catalogue) + 11 evaluator flags.
    expect(Object.keys(knobGuides)).toHaveLength(110);
  });

  it("generator guides are the setting catalogue's, links included; evaluator guides are written in config.ts", () => {
    for (const k of KNOBS) {
      const generated = (generatorGuides as Readonly<Record<string, GeneratedGuide>>)[k.id];
      if (k.channel === "evaluation") {
        expect(generated, k.id).toBeUndefined();
        continue;
      }
      if (!generated) throw new Error(`${k.id}: no generated guide`);
      const { refs, ...prose } = generated;
      const guide = knobGuides[k.id as KnobId];
      expect(guide, k.id).toEqual(expect.objectContaining(prose));
      expect(
        guide.refs?.map((r) => r.label),
        k.id,
      ).toEqual(refs?.map((r) => r.label));
    }
    // The knobs new since master carry catalogue prose too.
    expect(knobGuides.run_id.down).toBe("Required, and a flag only — a config file may not set it.");
    expect(knobGuides.ddl_uri.purpose).toMatch(/^A pinned _ddl\.json read only when live schema extraction fails/);
    // The serving launch keys are settings now; their longer story is the catalogue's tip.
    expect(knobGuides.gpu.purpose).toBe("The workers' GPU; a null vllm_dtype follows it.");
    expect(knobGuides.launcher_machine_type.tip).toContain("12-minute launch limit");
    // A paper keeps its URL, an external doc its URL, a repo doc resolves at the exported commit.
    expect(knobGuides.reference_rows_limit.refs).toEqual([
      expect.objectContaining({ kind: "paper", url: "https://doi.org/10.1214/aoms/1177728174" }),
      expect.objectContaining({ kind: "paper", url: "https://doi.org/10.1214/aop/1176990746" }),
      {
        label: "Article 2 — the 10k sample, its knobs and their aftermath",
        url: `https://github.com/albertols/synthetic-llm-dataflow-bigquery/blob/${CODE_REF}/docs/articles/02-type-system-freetext-resolution.md#L262`,
        kind: "docs",
      },
    ]);
    expect(knobGuides.pool_parallel_choices.refs).toEqual([
      {
        label: "vLLM automatic prefix caching",
        url: "https://docs.vllm.ai/en/stable/design/prefix_caching/",
        kind: "docs",
      },
    ]);
  });

  it("every ADR the knobs and annotations cite has a file in the ADR map", () => {
    const cited = new Set([...KNOBS.flatMap((k) => k.related_adrs), ...ANNOTATIONS.flatMap((a) => a.related_adrs)]);
    for (const number of cited) expect(ADRS[number], `ADR ${number}`).toBeDefined();
  });

  it("classifies constants and config-file-only settings as screws, derived values as readouts and every flag as a dial, a selector or a jack", () => {
    for (const k of KNOBS) {
      const kind = kindOf(k);
      if (k.settable_via.includes("constant")) expect(kind, k.id).toBe("screw");
      else if (k.settable_via.includes("derived")) expect(kind, k.id).toBe("readout");
      else if (k.settable_via.every((via) => via === "config")) expect(kind, k.id).toBe("screw");
      else expect(["dial", "selector", "port"], k.id).toContain(kind);
    }
    expect(knob("free_text_pool_max").settable_via).toEqual(["config"]);
    expect(kindOf(knob("free_text_pool_max"))).toBe("screw");
    expect(kindOf(knob("reference_rows_limit"))).toBe("dial");
    expect(kindOf(knob("source_stats"))).toBe("selector");
    // The evaluator's flags are read from its CLI like any other: none is a placeholder.
    expect(kindOf(knob("eval_scope"))).toBe("selector");
    expect(kindOf(knob("eval_sample_rows"))).toBe("port");
    for (const k of knobsFile.knobs.filter((k) => k.channel === "evaluation"))
      expect(k.source, k.id).toMatch(/^packages\/sdfb-evaluation\/src\/sdfb_evaluation\/cli\/main\.py:\d+$/);
  });

  it("says how a knob is set: a config key, a fixed constant, or the flags", () => {
    expect(settableText(knob("pool_values_per_call"))).toBe(
      "Set in a config file as free_text.pool_values_per_call — no flag.",
    );
    expect(settableText(knob("profiler_version"))).toBe("Fixed constant: not settable, change it in code.");
    expect(settableText(knob("env"))).toBe("Settable via CLI, Flex, Config.");
    expect(settableText(knob("run_id"))).toBe("Settable via CLI, Flex; free-form, not turnable here.");
  });

  it("a selector whose default is none of its choices starts on a position of its own", () => {
    const mode = knob("eval_mode");
    expect(mode.value).toBeNull();
    expect(positions(mode, mode.value)).toEqual({ values: [null, "exact", "sampled"], index: 0 });
    expect(positions(mode, "sampled")?.index).toBe(2);
    expect(positions(knob("source_stats"), "exact")).toEqual({ values: ["off", "sample", "exact"], index: 2 });
  });

  it("dial detents include the code default and any typed value", () => {
    for (const k of KNOBS.filter((k) => kindOf(k) === "dial" && typeof k.value === "number"))
      expect(dialSteps(k, k.value as number), k.id).toContain(k.value);
    expect(dialSteps(knob("num_rows"), 90_000_000)).toContain(90_000_000);
    expect(positions(knob("num_rows"), 90_000_000)?.values[positions(knob("num_rows"), 90_000_000)!.index]).toBe(
      90_000_000,
    );
  });

  it("selector choices come from argparse, env tiers from thresholds.yml", () => {
    expect(choicesOf(knob("uniqueness_mode"))).toEqual(["exact", "exact_chained", "streaming"]);
    expect(choicesOf(knob("env"))).toEqual(["dev", "uat", "prd"]);
    expect(choicesOf(knob("seed"))).toBeNull();
  });

  it("formats values without inventing any", () => {
    expect(formatKnobValue(knob("reference_rows_limit"))).toBe("10,000 rows");
    expect(formatKnobValue(knob("num_rows"))).toBe("1,000 rows");
    expect(formatKnobValue(knob("run_id"))).toBe("required");
    expect(formatKnobValue(knob("seed"))).toBe("(empty)");
    expect(shortValue(knob("temperature_ladder"))).toBe("0.7·1·1.3");
    expect(shortValue(knob("sampling_ladder"))).toBe("3 items");
    expect(asText(knob("default_embedder_identity").value)).toBe('["hashing-384","v1"]');
  });

  it("links resolve at the exported commit; measured values carry their MEASURED block", () => {
    expect(CODE_REF).toMatch(/^[0-9a-f]{40}$|^master$/);
    expect(sourceUrl("config/thresholds.yml:33")).toMatch(/\/blob\/.+\/config\/thresholds\.yml#L33$/);
    expect(docTitle("docs/designs/2026-07-24-reference-sample-scaling.md")).toBe(
      "Reference sample scaling (2026-07-24)",
    );
    for (const m of MEASURED) {
      expect(m.block, m.id).toMatch(/^MEASURED/);
      expect(m.source, m.id).toMatch(/^scripts\/doc\/.+\.py:\d+$/);
    }
  });
});
