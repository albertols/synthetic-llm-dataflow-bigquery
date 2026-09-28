import { describe, expect, it } from "vitest";

import { concepts, knobGuides } from "@contracts/concepts/config";
import { knobs as knobsFile } from "@contracts/generated/knobs";

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
  });

  it("every ADR the knobs and annotations cite has a file in the ADR map", () => {
    const cited = new Set([...KNOBS.flatMap((k) => k.related_adrs), ...ANNOTATIONS.flatMap((a) => a.related_adrs)]);
    for (const number of cited) expect(ADRS[number], `ADR ${number}`).toBeDefined();
  });

  it("classifies constants as screws, derived values as readouts and the evaluator flags as planned", () => {
    for (const k of KNOBS) {
      const kind = kindOf(k);
      if (k.source === "planned") expect(kind, k.id).toBe("planned");
      else if (k.settable_via.includes("constant")) expect(kind, k.id).toBe("screw");
      else if (k.settable_via.includes("derived")) expect(kind, k.id).toBe("readout");
      else expect(["dial", "selector", "port"], k.id).toContain(kind);
    }
    expect(kindOf(knob("free_text_pool_max"))).toBe("screw");
    expect(kindOf(knob("reference_rows_limit"))).toBe("dial");
    expect(kindOf(knob("source_stats"))).toBe("selector");
    expect(kindOf(knob("eval_mode"))).toBe("planned");
    expect(knobsFile.knobs.filter((k) => k.channel === "evaluation").every((k) => k.source === "planned")).toBe(true);
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
    expect(formatKnobValue(knob("num_rows"))).toBe("required");
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
