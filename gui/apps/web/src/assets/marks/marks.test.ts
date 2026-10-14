/**
 * The official marks are shown unaltered: each file here is byte-identical to
 * its source, as gui/ATTRIBUTION.md ("Official marks") records it by SHA-256.
 * A changed file, or one the table does not list, fails here.
 */
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

function guiRoot(): string {
  let dir = process.cwd();
  while (!existsSync(join(dir, "ATTRIBUTION.md")) || !existsSync(join(dir, "apps", "web"))) {
    const parent = dirname(dir);
    if (parent === dir) throw new Error("gui/ not found above the working directory");
    dir = parent;
  }
  return dir;
}

const GUI = guiRoot();
const MARKS = join(GUI, "apps", "web", "src", "assets", "marks");
const ATTRIBUTION = readFileSync(join(GUI, "ATTRIBUTION.md"), "utf8");

/** `file` → SHA-256 from the "Official marks" table. */
function recorded(): Map<string, string> {
  const section = ATTRIBUTION.slice(ATTRIBUTION.indexOf("### Official marks"));
  return new Map(
    [...section.matchAll(/^\| `([^`]+\.svg)`\s*\| `([0-9a-f]{64})`/gm)].map((m) => [m[1]!, m[2]!] as const),
  );
}

describe("official marks", () => {
  const files = readdirSync(MARKS).filter((name) => name.endsWith(".svg"));
  const table = recorded();

  it("are all listed in ATTRIBUTION.md, and nothing listed is missing", () => {
    expect(files.sort()).toEqual([...table.keys()].sort());
  });

  it.each(files)("%s is byte-identical to its source", (name) => {
    const sha = createHash("sha256")
      .update(readFileSync(join(MARKS, name)))
      .digest("hex");
    expect(sha).toBe(table.get(name));
  });

  it.each(files)("%s carries no script and no external reference", (name) => {
    const svg = readFileSync(join(MARKS, name), "utf8");
    expect(svg).not.toMatch(/<script|<foreignObject|\son[a-z]+\s*=|(?:xlink:)?href\s*=\s*["']\s*(?:https?:)?\/\//i);
  });
});
