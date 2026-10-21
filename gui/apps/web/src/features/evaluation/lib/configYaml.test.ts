import { parse } from "yaml";
import { describe, expect, it } from "vitest";

import { generationRecord } from "../test/fixtures";
import { configYaml } from "./configYaml";

describe("configYaml", () => {
  it("is the snapshot's values under a header naming the run and the version", () => {
    const record = generationRecord();
    const text = configYaml(record)!;
    expect(text.split("\n")[0]).toMatch(/^# Generator config of run B-00-users/);
    expect(text).toContain("0.6.1-master-edcfdcd");
    expect(text).not.toContain("Every key is pinned");
    expect(text.replace(/\n# /g, " ")).toContain(
      "Every resolved value is pinned except derived defaults (null) and the run id",
    );
    const parsed = parse(text, { version: "1.1" }) as unknown;
    expect(parsed).toEqual({ config_version: 1, ...record.snapshot!.values });
  });
  it("quotes what YAML 1.1 would read as a boolean or a number", () => {
    // The generator reads the file with PyYAML (YAML 1.1): `off` must stay the string "off", and
    // "8192" the string it is in the model.
    const parsed = parse(configYaml(generationRecord())!, { version: "1.1" }) as Record<
      string,
      Record<string, unknown>
    >;
    expect(parsed.free_text?.prompt_constraints).toBe("off");
    expect(parsed.serving?.vllm_max_model_len).toBe("8192");
  });
  it("is null without a snapshot", () => {
    expect(configYaml({ ...generationRecord(), snapshot: null })).toBeNull();
  });
});
