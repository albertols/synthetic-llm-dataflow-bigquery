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
import { dlqRuleById, dlqRules } from "./generated/dlqRules";
import { knobs } from "./generated/knobs";
import { relationships } from "./generated/relationships";
import { bqTables, evaluationMetricsRowSchema, rowSchemas, vocabularies } from "./generated/schemas";
import { diffGenerated, generate, main } from "./gen.mjs";
import { knobsFileSchema } from "./src/knobs.schema";
import { findEdge, formatEdge, parseEdge } from "./src/relational";
import { effectiveTier, snapshotKey } from "./src/sourceStats";
import { canonicalTimestamp, isCanonicalTimestamp, timestampFromEpochSeconds } from "./src/timestamps";

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
    const { repo: repo3, out: out3 } = copyInputs();
    const rules = join(out3, "dlq_rules.json");
    writeFileSync(rules, readFileSync(rules, "utf8").replace('"severity": "BLOCKER"', '"severity": "INFO"'));
    expect(diffGenerated(generate({ repo: repo3, inputsDir: out3 }), out3)).toEqual(["dlqRules.ts", "manifest.json"]);
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
    expect(knobs.exported_from.commit).toMatch(/^[0-9a-f]{40}$/);
  });

  it("the relationship model carries the thelook example with its edge roles", () => {
    const thelook = relationships.models.find((m) => m.model === "gcp_public_thelook");
    expect(thelook?.source).toBe("config/relationships/gcp_public_fk_example.yaml");
    expect(thelook?.generation_order).toEqual(["users", "orders", "order_items"]);
    const items = thelook?.tables.find((t) => t.name === "order_items");
    expect(items?.fk.map((e) => [formatEdge("order_items", e), e.role])).toEqual([
      ["order_items(order_id,user_id) -> orders(order_id,user_id)", "driving"],
      ["order_items(user_id) -> users(id)", "implied"],
      ["order_items(product_id) -> synthetic_data.products(id)", "external"],
    ]);
  });

  it("the DLQ rule map names the step each DoFn's rule lands under", () => {
    const step = (id: string) => dlqRuleById.get(id)?.pipeline_step ?? null;
    expect(step("fk.orphan")).toBe("EnforceFkIntegrityDoFn");
    expect(step("fk.unmatched")).toBe("GenerateRecordsDoFn");
    expect(step("schema.types")).toBe("ValidateRecordDoFn");
    expect(step("schema.batch")).toBe("PanderaValidateBatchDoFn");
    expect(step("pk.duplicate")).toBe("EnforceUniqueness");
    expect(dlqRuleById.get("fk.orphan")?.error_type).toBe("referential_integrity");
    expect(dlqRuleById.get("null.required")).toMatchObject({ emitted: false, declared: true, severity: "BLOCKER" });
    expect(dlqRules.rules.every((r) => r.emitted || r.declared)).toBe(true);
  });
});

describe("edge labels", () => {
  it("round-trip the evaluator form, composite and external parents included", () => {
    for (const label of [
      "orders(user_id) -> users(id)",
      "order_items(order_id,user_id) -> orders(order_id,user_id)",
      "order_items(product_id) -> synthetic_data.products(id)",
    ]) {
      const ref = parseEdge(label);
      expect(ref).not.toBeNull();
      if (!ref?.child || !ref.parentCols) throw new Error(label);
      expect(formatEdge(ref.child, { cols: ref.cols, ref: ref.parent, ref_cols: ref.parentCols })).toBe(label);
    }
    expect(parseEdge("order_items(product_id) -> synthetic_data.products(id)")).toEqual({
      child: "order_items",
      cols: ["product_id"],
      parent: "synthetic_data.products",
      parentCols: ["id"],
    });
  });

  it("parse the launcher form and reject malformed labels", () => {
    expect(parseEdge("(order_id,user_id)->orders")).toEqual({
      child: null,
      cols: ["order_id", "user_id"],
      parent: "orders",
      parentCols: null,
    });
    for (const bad of [
      "",
      "users",
      "a(b) -> c",
      "a(x,y) -> b(z)",
      "a(b) -> c(d) -> e(f)",
      "a(b) -> ",
      "(a b)->c",
      "orders.user_id->users.id",
    ])
      expect(parseEdge(bad), bad).toBeNull();
  });

  it("resolve against the model, a widened driving edge included", () => {
    const thelook = relationships.models.find((m) => m.model === "gcp_public_thelook");
    if (!thelook) throw new Error("no thelook model");
    expect(findEdge(thelook, "order_items(user_id) -> users(id)")?.edge.role).toBe("implied");
    expect(findEdge(thelook, "(order_id,user_id)->orders")?.table.name).toBe("order_items");
    expect(findEdge(thelook, "orders(user_id) -> users(id)")?.edge.role).toBe("driving");
    expect(findEdge(thelook, "orders(user_id) -> customers(id)")).toBeNull();
  });
});

describe("canonical timestamps", () => {
  it("keep BigQuery's microseconds from every form the client returns", () => {
    const at = "2026-09-01T10:00:00.123456Z";
    for (const text of [
      at,
      "2026-09-01T10:00:00.123456000Z", // PreciseDate#toISOString
      "2026-09-01 10:00:00.123456 UTC", // BigQuery text output
      "2026-09-01T12:00:00.123456+02:00",
      "2026-09-01T09:30:00.123456-00:30",
    ])
      expect(canonicalTimestamp(text), text).toBe(at);
    expect(canonicalTimestamp("2026-09-01T10:00:00.123Z")).toBe("2026-09-01T10:00:00.123000Z");
    expect(canonicalTimestamp("2026-09-01T10:00:00Z")).toBe("2026-09-01T10:00:00.000000Z");
    expect(canonicalTimestamp("2026-09-01")).toBe("2026-09-01T00:00:00.000000Z");
    expect(canonicalTimestamp("yesterday")).toBeNull();
    expect(timestampFromEpochSeconds(1_788_256_800.123456)).toBe("2026-09-01T10:00:00.123456Z");
    expect(isCanonicalTimestamp(at)).toBe(true);
    expect(isCanonicalTimestamp("2026-09-01T10:00:00.123Z")).toBe(false);
  });

  it("sort as strings in time order (the providers compare them that way)", () => {
    const texts = ["2026-09-01T10:00:00.000001Z", "2026-09-01T10:00:00Z", "2026-09-01T09:59:59.999999Z"];
    const canonical = texts.map((t) => canonicalTimestamp(t)!);
    expect([...canonical].sort()).toEqual([canonical[2], canonical[1], canonical[0]]);
  });
});

describe("source-stats snapshots", () => {
  it("are keyed by digest, tier, profiler version and run; NULL tier is sample", () => {
    const row = { reference_digest: "d", stats_tier: null, profiler_version: null, run_id: "r" };
    expect(effectiveTier(null)).toBe("sample");
    expect(snapshotKey(row)).toBe("d|sample||r");
    expect(snapshotKey({ ...row, stats_tier: "sample" })).toBe(snapshotKey(row));
    expect(snapshotKey({ ...row, stats_tier: "exact", profiler_version: "2" })).toBe("d|exact|2|r");
  });
});
