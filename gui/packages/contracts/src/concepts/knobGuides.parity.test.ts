import { describe, expect, it } from "vitest";

import master from "./__parity__/knob-guides.master.json";
import { knobGuides } from "./config";

/** The tip the catalogue gives a constant that a config file can now set (A1 plan Task 3). */
const PROMOTED_TIP = "Set it in a config file (--config_uri); a cache it shapes is rebuilt under the new value.";
/** The tip those constants carried on master. */
const MASTER_TIP = "A constant: changing it is a code edit, a test run and an image rebuild.";

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
      tip: guide.tip === PROMOTED_TIP ? MASTER_TIP : guide.tip,
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
});
