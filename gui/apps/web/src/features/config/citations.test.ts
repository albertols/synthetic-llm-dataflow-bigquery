/**
 * Every hand-typed `path:line` must still point at its excerpt in this
 * worktree: Guardrails.tsx and the rest of the tab cite through CITES, and
 * the concept file's code links with a line are checked too.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

import { CITES, splitCitation } from "./citations";

/** The repository root: the nearest ancestor of the working directory holding gui/ and packages/sdfb-beam/. */
function repoRoot(): string {
  let dir = process.cwd();
  while (!(existsSync(join(dir, "gui")) && existsSync(join(dir, "packages", "sdfb-beam")))) {
    const parent = dirname(dir);
    if (parent === dir) throw new Error("repository root not found above the working directory");
    dir = parent;
  }
  return dir;
}
const REPO = repoRoot();
const read = (path: string) => readFileSync(join(REPO, path), "utf8");

describe("hand-typed citations", () => {
  it.each(Object.entries(CITES))("%s is at its line", (_, cite) => {
    const { path, line } = splitCitation(cite.source);
    const text = read(path).split("\n")[line - 1] ?? "";
    expect(text, `${cite.source}`).toContain(cite.excerpt);
  });

  it("Guardrails.tsx and the scenario cite only through CITES (no raw path:line literals)", () => {
    for (const file of [
      "gui/apps/web/src/features/config/guardrails/Guardrails.tsx",
      "gui/apps/web/src/features/config/scenario/FidelityCards.tsx",
      "gui/apps/web/src/features/config/scenario/Article2Tables.tsx",
      "gui/apps/web/src/features/config/model/scenario.ts",
    ]) {
      const raw = read(file).match(/["'`][\w./-]+\.(?:py|md|yml|ts):\d+["'`]/g) ?? [];
      expect(raw, file).toEqual([]);
    }
  });

  it("the concept file's line-anchored links are all in CITES", () => {
    const concepts = read("gui/packages/contracts/src/concepts/config.ts");
    const anchored = [
      ...concepts.matchAll(/(?:code|doc)\("[^"]*",\s*"([^"]+)",\s*(\d+)\)|blob\("([^"]+)",\s*(\d+)\)/g),
    ].map((m) => `${m[1] ?? m[3]}:${m[2] ?? m[4]}`);
    expect(anchored.length).toBeGreaterThan(3);
    const known = new Set<string>(Object.values(CITES).map((c) => c.source));
    for (const source of anchored) expect(known.has(source), source).toBe(true);
  });
});
