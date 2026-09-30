/**
 * `.github/workflows/gui.yml` runs the GUI job only when GUI_PATHS matches a
 * changed file. The GUI's tests read repo files outside gui/ — quotes checked
 * verbatim, `path:line` citations, copied figures, contract sources — so each
 * of those files must match GUI_PATHS (and the workflow's `paths` trigger), or
 * a change to it would skip the job that would have failed.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

import { CITES, splitCitation } from "@/features/config/citations";
import { DESIGN_PATH, DESIGN_SECTIONS } from "@/features/intro/content/designSections";
import { PACKAGES } from "@/features/intro/content/packages";
import { STAGES } from "@/features/intro/content/stages";

function repoRoot(): string {
  let dir = process.cwd();
  while (!existsSync(join(dir, ".github", "workflows", "gui.yml"))) {
    const parent = dirname(dir);
    if (parent === dir) throw new Error(".github/workflows/gui.yml not found above the working directory");
    dir = parent;
  }
  return dir;
}

const REPO = repoRoot();
const WORKFLOW = readFileSync(join(REPO, ".github/workflows/gui.yml"), "utf8");

function envRegex(name: string): RegExp {
  const match = new RegExp(`${name}: '([^']+)'`).exec(WORKFLOW);
  if (!match) throw new Error(`${name} not found in gui.yml`);
  return new RegExp(match[1]!);
}

/** The `on.push.paths` globs, as regexes (`**` any depth, `*` one segment). */
function triggerGlobs(): RegExp[] {
  const block = WORKFLOW.slice(WORKFLOW.indexOf("paths: &all_paths"), WORKFLOW.indexOf("pull_request:"));
  return [...block.matchAll(/- "([^"]+)"/g)].map(
    (m) =>
      new RegExp(
        `^${m[1]!
          .replace(/[.+^${}()|[\]\\]/g, "\\$&")
          .replace(/\*\*/g, "\u0000")
          .replace(/\*/g, "[^/]*")
          .replaceAll("\u0000", ".*")}$`,
      ),
  );
}

/** Repo-relative files the GUI's unit tests read outside gui/. */
function filesTheTestsRead(): string[] {
  const files = new Set<string>([DESIGN_PATH, "README.md"]);
  for (const cite of Object.values(CITES)) files.add(splitCitation(cite.source).path);
  for (const stage of STAGES) files.add(stage.quote.source);
  for (const section of DESIGN_SECTIONS)
    for (const quote of [section.quote, ...(section.more ?? [])])
      files.add(quote.source === "DESIGN.md" ? DESIGN_PATH : quote.source);
  for (const pkg of PACKAGES) files.add(pkg.descriptionSource);
  // assets:check copies these figures.
  const assets = readFileSync(join(REPO, "gui/scripts/assets-sync.mjs"), "utf8");
  for (const m of assets.matchAll(/"(docs\/[^"]+\.(?:png|gif|svg|jpg))"/g)) files.add(m[1]!);
  // contracts:check reads these.
  files.add("packages/sdfb-evaluation/src/sdfb_evaluation/schemas/views.sql");
  files.add("packages/sdfb-evaluation/src/sdfb_evaluation/catalogue/metrics.yaml");
  files.add("config/bq_schema/synthetic_data_quality/validation_runs.schema.json");
  return [...files].filter((file) => !file.startsWith("gui/"));
}

/** A repo path, optionally with ":LINE", at the start of a string or inside prose ("… embedding.py (HashingEmbedder)"). */
const REPO_PATH =
  /(?:^|[\s"'(])((?:packages|scripts|config|composer|docker|docs)\/[\w./-]+\.(?:py|ya?ml|json|md|sql))(?::\d+)?/g;

function pathsIn(value: unknown, out: Set<string>): void {
  if (typeof value === "string") for (const m of value.matchAll(REPO_PATH)) out.add(m[1]!);
  else if (Array.isArray(value)) for (const item of value) pathsIn(item, out);
  else if (value && typeof value === "object") for (const item of Object.values(value)) pathsIn(item, out);
}

/**
 * What the Python exporters (scripts/gui/*.py) read or cite: the `REPO / "a" / "b"` paths in
 * their code (a directory stands for a file inside it), and every repo path their outputs
 * (knobs.json, relationships.json, dlq_rules.json, golden/*.json) cite — e.g. the docs/**.md
 * `_doc_paths` emits once it has checked the file exists.
 */
function filesTheExportersRead(): string[] {
  const files = new Set<string>();
  const scripts = readdirSync(join(REPO, "scripts/gui")).filter((f) => f.endsWith(".py"));
  for (const script of scripts) {
    files.add(`scripts/gui/${script}`);
    const code = readFileSync(join(REPO, "scripts/gui", script), "utf8");
    for (const m of code.matchAll(/REPO((?:\s*\/\s*"[^"]+")+)/g)) {
      const path = [...m[1]!.matchAll(/"([^"]+)"/g)].map((p) => p[1]).join("/");
      if (path.startsWith("gui/")) continue;
      const full = join(REPO, path);
      if (existsSync(full) && statSync(full).isDirectory()) files.add(`${path}/any.file`);
      else files.add(path);
    }
  }
  const generated = join(REPO, "gui/packages/contracts/generated");
  for (const file of ["knobs.json", "relationships.json", "dlq_rules.json"])
    pathsIn(JSON.parse(readFileSync(join(generated, file), "utf8")), files);
  for (const file of readdirSync(join(generated, "golden")).filter((f) => f.endsWith(".json")))
    pathsIn(JSON.parse(readFileSync(join(generated, "golden", file), "utf8")), files);
  return [...files].filter((file) => !file.startsWith("gui/"));
}

describe("gui.yml path filters", () => {
  const files = filesTheTestsRead();

  it("finds the files the tests read, and they exist", () => {
    expect(files.length).toBeGreaterThan(20);
    for (const file of files) expect(existsSync(join(REPO, file)), file).toBe(true);
  });

  it("GUI_PATHS matches every one of them", () => {
    const gui = envRegex("GUI_PATHS");
    expect(files.filter((file) => !gui.test(file))).toEqual([]);
  });

  it("the workflow's paths trigger covers every one of them", () => {
    const globs = triggerGlobs();
    expect(globs.length).toBeGreaterThan(10);
    expect(files.filter((file) => !globs.some((glob) => glob.test(file)))).toEqual([]);
  });

  it("EXPORTS_PATHS and the paths trigger cover every file the exporters read or cite", () => {
    const exporters = filesTheExportersRead();
    // The article the bge-pooling "docs differ" note quotes, and the scorer the scoring golden runs.
    expect(exporters).toContain("docs/articles/04-b1-rag-deep-dive.md");
    expect(exporters).toContain("packages/sdfb-evaluation/src/sdfb_evaluation/scoring/__init__.py");
    expect(exporters.some((f) => f.startsWith("docs/designs/"))).toBe(true);
    const exports = envRegex("EXPORTS_PATHS");
    expect(exporters.filter((file) => !exports.test(file))).toEqual([]);
    const globs = triggerGlobs();
    expect(exporters.filter((file) => !globs.some((glob) => glob.test(file)))).toEqual([]);
    // _doc_paths emits any cited docs/**.md: an ADR or article edit re-runs the exporter check.
    expect(exports.test("docs/adr/0042-self-hosted-platform-gui.md")).toBe(true);
    expect(exports.test("docs/articles/03-common-runtime.md")).toBe(true);
  });

  it("a Python change outside the cited files runs the exporter check, not the GUI job", () => {
    const gui = envRegex("GUI_PATHS");
    const exports = envRegex("EXPORTS_PATHS");
    const file = "packages/sdfb-core/src/sdfb_core/engines/b2_library/engine.py";
    expect(gui.test(file)).toBe(false);
    expect(exports.test(file)).toBe(true);
    expect(gui.test("gui/apps/web/src/main.tsx")).toBe(true);
  });
});
