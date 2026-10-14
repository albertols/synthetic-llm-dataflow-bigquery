import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { globToRegExp, matchesAny, parseOwnershipMap, violations } from "./check-owned-paths.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const map = parseOwnershipMap(readFileSync(resolve(HERE, "../docs/ARCHITECTURE.md"), "utf8"));

describe("check-owned-paths", () => {
  it("reads the ownership block of ARCHITECTURE.md", () => {
    expect(Object.keys(map.tabs).sort()).toEqual(["config", "evaluation", "intro", "rag"]);
    expect(map.foundation).toContain("gui/package.json");
  });

  it("matches globs the way the map uses them", () => {
    expect(globToRegExp("gui/apps/web/src/features/rag/**").test("gui/apps/web/src/features/rag/lab/Great.tsx")).toBe(
      true,
    );
    expect(globToRegExp("gui/tsconfig*.json").test("gui/tsconfig.base.json")).toBe(true);
    expect(globToRegExp("gui/*.md").test("gui/docs/UX.md")).toBe(false);
    expect(globToRegExp("gui/apps/web/{index.html,vite.config.ts}").test("gui/apps/web/vite.config.ts")).toBe(true);
  });

  it("lets a tab touch only its own paths", () => {
    const files = [
      "gui/apps/web/src/features/rag/RagPage.tsx",
      "gui/packages/contracts/src/concepts/rag.ts",
      "gui/e2e/rag.spec.ts",
      "gui/apps/server/src/routes/rag.extra.ts",
    ];
    expect(violations(files, "rag", map)).toEqual([]);
  });

  it("flags foundation files and other tabs' files", () => {
    const files = [
      "gui/apps/web/src/features/rag/RagPage.tsx",
      "gui/apps/web/src/components/InfoHint.tsx",
      "gui/package.json",
      "gui/apps/web/src/features/config/ConfigPage.tsx",
      "gui/packages/contracts/src/concepts/core.ts",
    ];
    expect(violations(files, "rag", map)).toEqual(files.slice(1));
    expect(matchesAny("gui/apps/web/src/components/InfoHint.tsx", map.foundation)).toBe(true);
  });

  it("gives INTRO no server route", () => {
    expect(violations(["gui/apps/server/src/routes/intro.extra.ts"], "intro", map)).toHaveLength(1);
  });
});
