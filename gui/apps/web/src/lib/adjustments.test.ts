import { describe, expect, it } from "vitest";

import { adjustmentVerdicts } from "@contracts/adjustments";
import { knobs } from "@contracts/generated/knobs";

import { fmtPoints, fmtRepeatShare, fmtTolerance, REPEAT_SHARE_TOLERANCE, verdictMark } from "./adjustments";

describe("the repeat-share tolerance", () => {
  it("is the code's REPEAT_SHARE_TOLERANCE (the repeat_share_tolerance knob), not a number of the GUI's", () => {
    const knob = knobs.knobs.find((k) => k.id === "repeat_share_tolerance")!;
    expect(knob.source_token).toBe("REPEAT_SHARE_TOLERANCE");
    expect(REPEAT_SHARE_TOLERANCE).toBe(knob.value);
    expect(fmtTolerance()).toBe("±5 pt");
    expect(fmtTolerance(0.025)).toBe("±2.5 pt");
  });
});

describe("shares and their differences in words", () => {
  it("prints a share with one decimal, so two shares side by side compare digit for digit", () => {
    expect(fmtRepeatShare(0.5027)).toBe("50.3%");
    expect(fmtRepeatShare(0.4197)).toBe("42.0%");
    expect(fmtRepeatShare(0)).toBe("0.0%");
    expect(fmtRepeatShare(null)).toBe("—");
    expect(fmtRepeatShare(Number.NaN)).toBe("—");
  });

  it("prints a difference of two shares in percentage points, signed", () => {
    expect(fmtPoints(-0.2419)).toBe("−24.2 pt");
    expect(fmtPoints(0.1271)).toBe("+12.7 pt");
    // A delta that rounds to zero keeps its sign: the generator's own −0.0001.
    expect(fmtPoints(-0.0001)).toBe("−0.0 pt");
    expect(fmtPoints(0)).toBe("±0.0 pt");
    expect(fmtPoints(null)).toBe("—");
  });
});

describe("verdictMark: what each verdict shows", () => {
  it("covers every verdict of the contract", () => {
    const marks = Object.fromEntries(adjustmentVerdicts.map((v) => [v, verdictMark(v)]));
    expect(marks.not_adjusted).toBeNull();
    expect(marks.faithful).toMatchObject({ kind: "adjusted", tone: "info", label: "Adjusted · faithful" });
    expect(marks.under).toMatchObject({ kind: "adjusted", tone: "warn", label: "Adjusted · under" });
    expect(marks.over).toMatchObject({ kind: "adjusted", tone: "warn", label: "Adjusted · over" });
    expect(marks.unmeasured).toMatchObject({ kind: "question", tone: null, label: "Adjusted?" });
    expect(marks.unknown).toMatchObject({ kind: "question", tone: null, label: "Adjusted?" });
    // The two questions are different questions.
    expect(marks.unmeasured!.long).not.toBe(marks.unknown!.long);
    for (const mark of Object.values(marks)) if (mark) expect(mark.long).toMatch(/ADR 0038/);
  });
});
