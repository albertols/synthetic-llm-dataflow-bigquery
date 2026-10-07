#!/usr/bin/env node
// assets:sync — copies the repo figures the GUI shows into
// apps/web/public/assets/ and writes provenance.json next to them (source
// path, SHA-256, the script or draw.io file that makes the figure, and any
// third-party marks it carries). The copies are committed; git stores one
// blob for identical bytes, so they cost no repository space.
//
//   node scripts/assets-sync.mjs           # copy + write provenance.json
//   node scripts/assets-sync.mjs --check   # exit 1 when a copy or provenance.json is stale
//
// A figure is never edited here: regenerate it with its script under
// scripts/doc/ (or re-export the .drawio), then re-run assets:sync.
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "../..");
const OUT = resolve(HERE, "../apps/web/public/assets");

/** The figures the tabs use, grouped by the tab that needs them (any tab may use any). */
export const ASSETS = [
  // DESIGN.md §1–§8 (INTRO "How it works" cards).
  "docs/assets/architecture-overview.png",
  "docs/designs/assets/fanout-generation.png",
  "docs/designs/assets/multi-parent-candidate-cap.png",
  "docs/designs/assets/fk-orphan-rate.png",
  "docs/designs/assets/stats-inverse-cdf.png",
  "docs/designs/assets/throughput-where-time-went.png",
  "docs/designs/assets/relationships-scenarios.png",
  "docs/designs/assets/relationships-flags.png",
  "docs/designs/assets/throughput-evolution.png",
  // Reference-sample scaling (CONFIG scenario calculator).
  "docs/designs/assets/sampling-error-dkw.png",
  "docs/designs/assets/rare-category-coverage.png",
  "docs/designs/assets/tail-support.png",
  "docs/designs/assets/identifier-collisions.png",
  "docs/designs/assets/pk-capacity-random-draws.png",
  // Source statistics (CONFIG source-stats explorer).
  "docs/designs/assets/stats-entropy-skew.png",
  "docs/designs/assets/stats-epoch-deciles.png",
  "docs/designs/assets/stats-null-patterns.png",
  // RAG geometry (RAG tab).
  "docs/designs/assets/rag-dense-vectors-3d.png",
  "docs/designs/assets/rag-great-serialization.png",
  "docs/designs/assets/rag-retrieval-sphere.gif",
  "docs/designs/assets/rag-seed-pickers.png",
  "docs/designs/assets/rag-seed-budget.png",
  "docs/designs/assets/rag-setup-cost.png",
  "docs/designs/assets/rag-fidelity-originality.png",
  "docs/designs/assets/prefix-vs-kcenter-coverage.png",
  "docs/designs/assets/embedding-geometry-topk.png",
  "docs/designs/assets/centroid-vs-perquery.png",
  // DESIGN.md §11 (the INTRO card of the evaluation).
  "docs/designs/assets/eval-noise-floor.png",
  // Evaluation (EVALUATION tab).
  "docs/designs/assets/eval-dcr-nndr.png",
  "docs/designs/assets/eval-ks-vs-wasserstein.png",
  // Article diagrams (draw.io exports).
  "docs/articles/assets/beam-firefly-mascot.png",
  "docs/articles/assets/rag-end-to-end-flow.png",
  "docs/articles/assets/rag-faiss-data-path.png",
  "docs/articles/assets/rag-chunk-identity.png",
  "docs/articles/assets/validation-guardrails.png",
  "docs/articles/assets/generation-plan-routing.png",
  "docs/articles/assets/freetext-pool-ladder.png",
  "docs/articles/assets/runtime-fleet-topology.png",
  "docs/articles/assets/runtime-cpu-gpu-sequence.png",
];

const MARKS_NOTE =
  "draw.io diagram that may embed the Apache Beam firefly mascot (© The Apache Software Foundation) and Google Cloud product icons; see gui/ATTRIBUTION.md.";

/**
 * @typedef {{ file: string; source: string; sha256: string; bytes: number; generator: string | null;
 *   documentedIn: string | null; attribution?: string }} AssetEntry
 */

/** @param {string} path */
function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

/** DESIGN.md §10 maps committed figures to the script that draws them. */
function designGenerators() {
  /** @type {Map<string, string>} */
  const map = new Map();
  const design = readFileSync(join(REPO, "docs/DESIGN.md"), "utf8");
  for (const match of design.matchAll(/^\| `([^`]+\.(?:png|gif))` \| `([^`]+)`/gm)) {
    const [, figure = "", script = ""] = match;
    const generator = script.startsWith("scripts/") ? script : `docs/${script}`;
    map.set(`docs/${figure}`, generator);
  }
  return map;
}

/**
 * The first doc that shows the figure: DESIGN.md, then design docs, then articles.
 * @param {string} file
 * @param {readonly string[]} docs
 */
function documentedIn(file, docs) {
  const name = basename(file);
  return docs.find((doc) => readFileSync(join(REPO, doc), "utf8").includes(name)) ?? null;
}

/** @param {string} dir */
function listMarkdown(dir) {
  const abs = join(REPO, dir);
  return existsSync(abs)
    ? readdirSync(abs)
        .filter((f) => f.endsWith(".md"))
        .sort()
        .map((f) => `${dir}/${f}`)
    : [];
}

/**
 * @param {string} file
 * @param {readonly string[]} scripts
 */
function scriptThatWrites(file, scripts) {
  const name = basename(file);
  return scripts.find((script) => readFileSync(join(REPO, script), "utf8").includes(name));
}

export function buildProvenance() {
  const fromDesign = designGenerators();
  const scriptDir = join(REPO, "scripts/doc");
  const scripts = existsSync(scriptDir)
    ? readdirSync(scriptDir)
        .filter((f) => f.endsWith(".py"))
        .sort()
        .map((f) => `scripts/doc/${f}`)
    : [];
  const docs = ["docs/DESIGN.md", ...listMarkdown("docs/designs"), ...listMarkdown("docs/articles")];
  const names = new Set();
  const assets = ASSETS.map((source) => {
    const abs = join(REPO, source);
    if (!existsSync(abs)) throw new Error(`asset source missing: ${source}`);
    const file = basename(source);
    if (names.has(file)) throw new Error(`two sources share the file name ${file}`);
    names.add(file);
    const drawio = source.replace(/\.png$/, ".drawio");
    const generator =
      fromDesign.get(source) ??
      (existsSync(join(REPO, drawio)) && drawio !== source ? drawio : undefined) ??
      scriptThatWrites(source, scripts) ??
      null;
    /** @type {AssetEntry} */
    const entry = {
      file,
      source,
      sha256: sha256(abs),
      bytes: readFileSync(abs).length,
      generator,
      documentedIn: documentedIn(source, docs),
    };
    if (file === "beam-firefly-mascot.png") {
      entry.attribution =
        "Apache Beam firefly mascot © The Apache Software Foundation (beam.apache.org/community/mascot/); identifies Apache Beam only. Apache Beam™ is a trademark of the ASF.";
    } else if (generator?.endsWith(".drawio")) {
      entry.attribution = MARKS_NOTE;
    }
    return entry;
  });
  return {
    note: "Generated by gui/scripts/assets-sync.mjs (npm run assets:sync). Do not edit; re-run the script.",
    assets,
  };
}

/** @param {string[]} argv */
function main(argv) {
  const check = argv.includes("--check");
  const provenance = buildProvenance();
  const json = `${JSON.stringify(provenance, null, 2)}\n`;
  const provenancePath = join(OUT, "provenance.json");
  if (check) {
    const stale = [];
    if (!existsSync(provenancePath) || readFileSync(provenancePath, "utf8") !== json) stale.push("provenance.json");
    for (const asset of provenance.assets) {
      const copy = join(OUT, asset.file);
      if (!existsSync(copy) || sha256(copy) !== asset.sha256) stale.push(asset.file);
    }
    if (stale.length) {
      console.error(`assets:check: ${stale.length} stale file(s) in ${relative(REPO, OUT)}: ${stale.join(", ")}`);
      console.error("Run `npm run assets:sync` and commit the result.");
      return 1;
    }
    console.log(`assets:check: ${provenance.assets.length} figures match their sources.`);
    return 0;
  }
  mkdirSync(OUT, { recursive: true });
  for (const asset of provenance.assets) copyFileSync(join(REPO, asset.source), join(OUT, asset.file));
  writeFileSync(provenancePath, json);
  console.log(`assets:sync: copied ${provenance.assets.length} figures to ${relative(REPO, OUT)} (+ provenance.json).`);
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exit(main(process.argv.slice(2)));
}
