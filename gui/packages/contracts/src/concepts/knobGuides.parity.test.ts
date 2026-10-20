import { describe, expect, it } from "vitest";
import { knobGuides } from "./config";

/** Commit-free view of a guide: code/doc links lose the exported sha. */
function serialise(): unknown {
  const out: Record<string, unknown> = {};
  for (const [id, guide] of Object.entries(knobGuides)) {
    out[id] = {
      ...guide,
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
});
