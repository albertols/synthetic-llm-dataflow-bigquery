import { describe, expect, it } from "vitest";

import { contractWarningsHeader } from "./context";

const parse = (header: string) => JSON.parse(header) as string[];

describe("x-contract-warnings", () => {
  it("is a JSON array, so a finding may contain '; '", () => {
    const findings = ['runs.list: status="A; B" not in the contract vocabulary (2 rows)', "dlq.summary: x"];
    expect(parse(contractWarningsHeader(findings))).toEqual(findings);
  });

  it("escapes non-ASCII so the header stays printable ASCII, and round-trips", () => {
    const findings = ['metrics.list: family="fidélité" not in the contract vocabulary', "emoji: 🧪"];
    const header = contractWarningsHeader(findings);
    expect(header).toMatch(/^[\x20-\x7e]+$/);
    expect(parse(header)).toEqual(findings);
  });

  it("shows five findings, clips long ones, says how many it left out, and stays under 1,000 characters", () => {
    const findings = Array.from({ length: 9 }, (_, i) => `finding ${i}: ${"x".repeat(400)}`);
    const header = contractWarningsHeader(findings);
    expect(header.length).toBeLessThanOrEqual(1000);
    const list = parse(header);
    expect(list.at(-1)).toMatch(/^… and \d+ more$/);
    const shown = list.length - 1;
    expect(Number(/and (\d+) more/.exec(list.at(-1)!)![1])).toBe(9 - shown);
    for (const finding of list.slice(0, -1)) expect(finding.length).toBeLessThanOrEqual(200);
  });
});
