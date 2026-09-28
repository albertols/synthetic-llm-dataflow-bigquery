/**
 * contracts:check must fail when the Python side changes and the GUI has not
 * re-synced: a schema field, a vocabulary in a description, the catalogue, or
 * the Python-owned knobs.json / golden files.
 */
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { catalogue, concepts } from "./generated/catalogue";
import { knobs } from "./generated/knobs";
import { bqTables, evaluationMetricsRowSchema, rowSchemas, vocabularies } from "./generated/schemas";
import { diffGenerated, generate, main } from "./gen.mjs";
import { knobsFileSchema } from "./src/knobs.schema";

const HERE = import.meta.dirname;
const REPO = resolve(HERE, "../../..");
const GENERATED = join(HERE, "generated");
const EVAL_SCHEMAS = "packages/sdfb-evaluation/src/sdfb_evaluation/schemas";
const CATALOGUE = "packages/sdfb-evaluation/src/sdfb_evaluation/catalogue/metrics.yaml";

const temps: string[] = [];
afterEach(() => {
  for (const dir of temps.splice(0)) rmSync(dir, { recursive: true, force: true });
});

/** A throwaway repo with only the generator's inputs, plus a copy of generated/. */
function copyInputs(): { repo: string; out: string } {
  const repo = mkdtempSync(join(tmpdir(), "contracts-repo-"));
  temps.push(repo);
  cpSync(join(REPO, EVAL_SCHEMAS), join(repo, EVAL_SCHEMAS), { recursive: true });
  cpSync(join(REPO, "config/bq_schema"), join(repo, "config/bq_schema"), { recursive: true });
  cpSync(join(REPO, CATALOGUE), join(repo, CATALOGUE));
  const out = join(repo, "generated");
  cpSync(GENERATED, out, { recursive: true });
  return { repo, out };
}

function editJson(path: string, edit: (value: unknown[]) => void) {
  const value = JSON.parse(readFileSync(path, "utf8")) as unknown[];
  edit(value);
  writeFileSync(path, JSON.stringify(value, null, 2));
}

describe("contracts:check", () => {
  it("the committed generated files match their inputs", () => {
    const files = generate({ repo: REPO, inputsDir: GENERATED });
    expect(diffGenerated(files, GENERATED)).toEqual([]);
  });

  it("an untouched copy passes --check", () => {
    const { repo, out } = copyInputs();
    expect(main(["--check", "--repo", repo, "--out", out])).toBe(0);
  });

  it("a new column in a copied schema is drift", () => {
    const { repo, out } = copyInputs();
    editJson(join(repo, EVAL_SCHEMAS, "evaluation_metrics.schema.json"), (fields) =>
      fields.push({ name: "new_column", type: "STRING", mode: "NULLABLE", description: "added on the Python side" }),
    );
    const drift = diffGenerated(generate({ repo, inputsDir: out }), out);
    expect(drift).toEqual(expect.arrayContaining(["schemas.ts", "manifest.json"]));
    expect(main(["--check", "--repo", repo, "--out", out])).toBe(1);
  });

  it("a changed type or mode in config/bq_schema is drift", () => {
    const { repo, out } = copyInputs();
    editJson(join(repo, "config/bq_schema/synthetic_rag/freetext_pools.schema.json"), (fields) => {
      (fields[0] as { mode: string }).mode = "NULLABLE";
    });
    expect(diffGenerated(generate({ repo, inputsDir: out }), out)).toContain("schemas.ts");
  });

  it("a vocabulary that no longer parses fails the generator loudly", () => {
    const { repo, out } = copyInputs();
    editJson(join(repo, EVAL_SCHEMAS, "evaluation_metrics.schema.json"), (fields) => {
      const status = fields.find((f) => (f as { name: string }).name === "status") as { description: string };
      status.description = "the metric status";
    });
    expect(() => generate({ repo, inputsDir: out })).toThrow(/evaluation_metrics\.status/);
  });

  it("a hand edit of knobs.json or a golden file is drift", () => {
    const { repo, out } = copyInputs();
    const knobsPath = join(out, "knobs.json");
    writeFileSync(knobsPath, readFileSync(knobsPath, "utf8").replace('"value": 1024', '"value": 2048'));
    expect(diffGenerated(generate({ repo, inputsDir: out }), out)).toEqual(
      expect.arrayContaining(["knobs.ts", "manifest.json"]),
    );
    const { repo: repo2, out: out2 } = copyInputs();
    const golden = join(out2, "golden", "retrieval.json");
    writeFileSync(golden, readFileSync(golden, "utf8") + " ");
    expect(diffGenerated(generate({ repo: repo2, inputsDir: out2 }), out2)).toEqual(["manifest.json"]);
  });

  it("a catalogue edit is drift", () => {
    const { repo, out } = copyInputs();
    const path = join(repo, CATALOGUE);
    writeFileSync(path, readFileSync(path, "utf8").replace('title: "Category adherence"', 'title: "Category fit"'));
    expect(diffGenerated(generate({ repo, inputsDir: out }), out)).toEqual(
      expect.arrayContaining(["catalogue.ts", "manifest.json"]),
    );
  });
});

describe("generated contracts", () => {
  it("cover every table the GUI reads", () => {
    expect(Object.keys(bqTables).sort()).toEqual(
      [
        "dlq",
        "evaluation_data_history",
        "evaluation_metrics",
        "evaluation_profiles",
        "evaluation_row_flags",
        "fk_fanout_stats",
        "freetext_pools",
        "rag_chunks",
        "source_table_stats",
        "validation_runs",
      ].sort(),
    );
    expect(Object.keys(rowSchemas).sort()).toEqual(Object.keys(bqTables).sort());
  });

  it("the metric row schema enforces REQUIRED, NULLABLE and the parsed vocabularies", () => {
    const row = {
      evaluation_id: "eval-1",
      evaluated_at: "2026-09-01T10:00:00Z",
      table_name: "orders",
      landing_table: null,
      source_table: null,
      level: "column",
      family: "fidelity",
      metric_id: "column.ks",
      metric_version: "1",
      value_kind: "distance",
      column_name: "num_of_item",
      column_name_2: null,
      column_kind: "numeric",
      edge: null,
      value: 0.04,
      source_value: null,
      synthetic_value: null,
      baseline_value: 0.01,
      score: 0.96,
      status: "pass",
      threshold_warn: 0.1,
      threshold_fail: 0.2,
      noise_floor: 0.0136,
      noise_floor_method: "ks_two_sample",
      ci_low: null,
      ci_high: null,
      n_source: 10000,
      n_synthetic: 10000,
      method: "binned",
      sample_rate: null,
      encoding_plan_digest: null,
      feature_set_digest: null,
      detail: { d_hi: 0.05 },
    };
    expect(evaluationMetricsRowSchema.safeParse(row).success).toBe(true);
    expect(evaluationMetricsRowSchema.safeParse({ ...row, status: "ok" }).success).toBe(false);
    expect(evaluationMetricsRowSchema.safeParse({ ...row, metric_id: null }).success).toBe(false);
    const { value: _dropped, ...missing } = row;
    expect(evaluationMetricsRowSchema.safeParse(missing).success).toBe(false);
    expect(vocabularies["evaluation_metrics.status"]).toEqual(["pass", "warn", "fail", "info", "not_evaluated"]);
  });

  it("the catalogue and its concepts line up one to one", () => {
    expect(catalogue).toHaveLength(79);
    expect(concepts.map((c) => c.id)).toEqual(catalogue.map((m) => `metric:${m.id}`));
    for (const concept of concepts) expect(concept.links.every((l) => l.url.startsWith("https://"))).toBe(true);
  });

  it("knobs.json validates against its schema", () => {
    const parsed = knobsFileSchema.safeParse(knobs);
    expect(parsed.success, parsed.success ? "" : JSON.stringify(parsed.error.issues.slice(0, 3))).toBe(true);
  });
});
