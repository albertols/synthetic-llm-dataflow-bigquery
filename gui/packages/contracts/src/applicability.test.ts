import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { inapplicableKey, NOT_APPLICABLE_LABEL, NO_IDENTITY_REASON, NO_PK_REASON } from "./applicability";

const MEMBERSHIP = fileURLToPath(
  new URL("../../../../packages/sdfb-evaluation/src/sdfb_evaluation/beam/membership.py", import.meta.url),
);

describe("key checks with nothing to check", () => {
  it("copies the evaluator's two reasons verbatim", () => {
    const source = readFileSync(MEMBERSHIP, "utf8");
    expect(source).toContain(`_NO_PK = "${NO_PK_REASON}"`);
    expect(source).toContain(`_NO_IDENTITY = "${NO_IDENTITY_REASON}"`);
  });

  it("names the key a not-evaluated row has nothing to check for", () => {
    expect(
      inapplicableKey({
        metric_id: "table.pk_duplicate_rate",
        status: "not_evaluated",
        detail: { reason: NO_PK_REASON },
      }),
    ).toBe("pk");
    expect(
      inapplicableKey({
        metric_id: "table.identity_duplicate_rate",
        status: "not_evaluated",
        detail: { reason: NO_IDENTITY_REASON },
      }),
    ).toBe("identity");
    expect(NOT_APPLICABLE_LABEL.pk).toBe("Not applicable — no primary key declared");
    expect(NOT_APPLICABLE_LABEL.identity).toBe("Not applicable — no identity columns declared");
  });

  it("leaves every other not-evaluated row alone", () => {
    // Sampled mode cannot count duplicates: a gap in the evaluation, not "nothing to check".
    expect(
      inapplicableKey({
        metric_id: "table.pk_duplicate_rate",
        status: "not_evaluated",
        detail: { reason: "sampled mode cannot measure primary-key duplicates" },
      }),
    ).toBeNull();
    expect(
      inapplicableKey({ metric_id: "table.pk_duplicate_rate", status: "pass", detail: { reason: NO_PK_REASON } }),
    ).toBeNull();
    expect(
      inapplicableKey({ metric_id: "column.ks", status: "not_evaluated", detail: { reason: NO_PK_REASON } }),
    ).toBeNull();
    expect(inapplicableKey({ metric_id: "table.pk_duplicate_rate", status: "not_evaluated", detail: null })).toBeNull();
    // The identity reason on the PK row is not the PK's reason.
    expect(
      inapplicableKey({
        metric_id: "table.pk_duplicate_rate",
        status: "not_evaluated",
        detail: { reason: NO_IDENTITY_REASON },
      }),
    ).toBeNull();
  });
});
