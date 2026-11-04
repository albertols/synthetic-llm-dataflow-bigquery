import { describe, expect, it } from "vitest";

import type { GeneratorSnapshot } from "@contracts/record";

import { generationRecord } from "../test/fixtures";
import { dataflowJobUrl, generationRegion, projectOf, shortVersion, toleranceOf } from "./record";

/** A record whose snapshot's `beam` block is `beam` (anything: the reader never trusts it). */
const withBeam = (beam: unknown) => {
  const record = generationRecord();
  return { ...record, snapshot: { ...record.snapshot!, beam } as GeneratorSnapshot };
};

describe("generationRegion (a job link's region)", () => {
  it("is the registry's generation_region when it has one", () => {
    expect(generationRegion("europe-west3", withBeam({ region: "us-central1" }))).toBe("europe-west3");
  });
  it("else the record snapshot's beam.region: a --run_id evaluation records no region (ADR 0045)", () => {
    expect(generationRegion(null, withBeam({ runner: "DataflowRunner", region: "europe-west3" }))).toBe("europe-west3");
  });
  it("is null when neither knows it: no registry region and no record", () => {
    expect(generationRegion(null, null)).toBeNull();
    expect(generationRegion(null, undefined)).toBeNull();
    expect(generationRegion(null, { ...generationRecord(), snapshot: null })).toBeNull();
  });
  it("is null for a record written before beam.region (A2's beam block)", () => {
    expect(generationRegion(null, withBeam({ runner: "DataflowRunner", job_name: "j" }))).toBeNull();
    expect(generationRegion(null, generationRecord())).toBeNull();
  });
  it("reads a malformed beam block as no region", () => {
    for (const beam of [null, "europe-west3", ["europe-west3"], 7, { region: 5 }, { region: "" }, { region: null }])
      expect(generationRegion(null, withBeam(beam)), JSON.stringify(beam)).toBeNull();
    expect(generationRegion("", withBeam({ region: "europe-west3" }))).toBe("europe-west3");
  });
});

describe("record links", () => {
  it("links a Dataflow job in its region and project", () => {
    expect(dataflowJobUrl("2026-10-21_01_02_03-123", "europe-west3", "p")).toBe(
      "https://console.cloud.google.com/dataflow/jobs/europe-west3/2026-10-21_01_02_03-123?project=p",
    );
  });
  it("reads the project of a table", () => {
    expect(projectOf("p.d.t")).toBe("p");
    expect(projectOf("p:d.t")).toBe("p");
    expect(projectOf("d.t")).toBeNull();
    expect(projectOf(null)).toBeNull();
  });
  it("shortens a version to base · sha", () => {
    expect(shortVersion("0.6.1-feat-config-record-model-1a2b3c4-dirty")).toBe("0.6.1 · 1a2b3c4*");
    expect(shortVersion(null)).toBe("—");
    expect(shortVersion("odd")).toBe("odd");
  });
});

describe("toleranceOf", () => {
  it("is the run's guardrails.repeat_share_tolerance", () => {
    expect(toleranceOf(generationRecord({ tolerance: 0.08 }))).toBe(0.08);
  });
  it("falls back to the default", () => {
    expect(toleranceOf(null)).toBe(0.05);
    expect(toleranceOf({ ...generationRecord(), snapshot: null })).toBe(0.05);
  });
});
