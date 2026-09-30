#!/usr/bin/env node
// Fails when a tab branch (feat/gui-<tab>) touches paths outside the tab's
// set. The ownership map is the JSON block in gui/docs/ARCHITECTURE.md
// (between the `owned-paths:start` / `owned-paths:end` markers), so the doc
// and the gate cannot drift apart.
//
//   node scripts/check-owned-paths.mjs                     # current branch vs feat/gui-synthetic-platform
//   node scripts/check-owned-paths.mjs feat/gui-rag --base origin/feat/gui-synthetic-platform
//
// Branches that are not tab branches pass (exit 0) with a note.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ARCHITECTURE = resolve(HERE, "../docs/ARCHITECTURE.md");
const DEFAULT_BASE = "feat/gui-synthetic-platform";
const TAB_BRANCH = /^(?:.*\/)?feat\/gui-([a-z]+)$/;

/** @typedef {{ foundation: string[]; tabs: Record<string, string[]> }} OwnershipMap */

/**
 * Reads the ownership JSON block out of ARCHITECTURE.md.
 * @param {string} markdown
 * @returns {OwnershipMap}
 */
export function parseOwnershipMap(markdown) {
  const match = /<!--\s*owned-paths:start\s*-->\s*```json\s*([\s\S]*?)```\s*<!--\s*owned-paths:end\s*-->/.exec(
    markdown,
  );
  if (!match)
    throw new Error(
      "ARCHITECTURE.md has no ```json block between <!-- owned-paths:start --> and <!-- owned-paths:end -->",
    );
  /** @type {OwnershipMap} */
  const map = JSON.parse(match[1] ?? "");
  if (!Array.isArray(map.foundation) || typeof map.tabs !== "object" || map.tabs === null) {
    throw new Error('ownership map must be { "foundation": string[], "tabs": { "<tab>": string[] } }');
  }
  return map;
}

/**
 * `**` spans directories, `*` stays in one segment, `{a,b}` alternates. Paths are repo-relative, "/"-separated.
 * @param {string} glob
 * @returns {RegExp}
 */
export function globToRegExp(glob) {
  let out = "";
  for (let i = 0; i < glob.length; i += 1) {
    const ch = glob.charAt(i);
    if (glob.startsWith("**/", i)) {
      out += "(?:.*/)?";
      i += 2;
    } else if (glob.startsWith("**", i)) {
      out += ".*";
      i += 1;
    } else if (ch === "*") {
      out += "[^/]*";
    } else if (ch === "?") {
      out += "[^/]";
    } else if (ch === "{") {
      const end = glob.indexOf("}", i);
      if (end === -1) throw new Error(`unclosed { in ${glob}`);
      out += `(?:${glob
        .slice(i + 1, end)
        .split(",")
        .map((part) => globToRegExp(part).source.slice(1, -1))
        .join("|")})`;
      i = end;
    } else {
      out += ch.replace(/[.+^$()|[\]\\]/g, "\\$&");
    }
  }
  return new RegExp(`^${out}$`);
}

/**
 * @param {string} path
 * @param {readonly string[]} globs
 */
export function matchesAny(path, globs) {
  return globs.some((glob) => globToRegExp(glob).test(path));
}

/**
 * The changed files a tab may not touch.
 * @param {readonly string[]} files
 * @param {string} tab
 * @param {OwnershipMap} map
 * @returns {string[]}
 */
export function violations(files, tab, map) {
  const owned = map.tabs[tab];
  if (!owned) throw new Error(`no ownership entry for tab "${tab}" (known: ${Object.keys(map.tabs).join(", ")})`);
  return files.filter((file) => !matchesAny(file, owned));
}

/** @param {string[]} args */
function git(args) {
  return execFileSync("git", args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
}

/**
 * Uncommitted paths (staged, unstaged, untracked), from `git status -z`
 * (NUL-separated, so no trimming or quoting can corrupt a path).
 * @returns {string[]}
 */
function worktreeChanges() {
  const out = execFileSync("git", ["status", "--porcelain=v1", "-z", "--untracked-files=all"], { encoding: "utf8" });
  const entries = out.split("\0");
  /** @type {string[]} */
  const paths = [];
  for (let i = 0; i < entries.length; i += 1) {
    const entry = entries[i] ?? "";
    if (entry.length < 4) continue;
    paths.push(entry.slice(3));
    // A rename or copy is followed by its original path.
    if (entry[0] === "R" || entry[0] === "C") i += 1;
  }
  return paths;
}

/**
 * The git ref holding the branch: the local branch, else its remote-tracking
 * ref, else HEAD (CI checks out a detached PR merge commit).
 * @param {string} branch
 */
function resolveRef(branch) {
  for (const ref of [branch, `origin/${branch}`]) {
    try {
      git(["rev-parse", "--verify", "--quiet", `${ref}^{commit}`]);
      return ref;
    } catch {
      // try the next candidate
    }
  }
  return "HEAD";
}

/**
 * @param {string} ref
 * @param {string} base
 * @param {boolean} includeWorktree
 * @returns {string[]}
 */
function changedFiles(ref, base, includeWorktree) {
  const mergeBase = git(["merge-base", base, ref]);
  const committed = git(["diff", "--name-only", mergeBase, ref]).split("\n");
  const worktree = includeWorktree ? worktreeChanges() : [];
  return [...new Set([...committed, ...worktree].filter(Boolean))].sort();
}

/** @param {string[]} argv */
function main(argv) {
  const args = [...argv];
  const baseIndex = args.indexOf("--base");
  const base = (baseIndex >= 0 ? args.splice(baseIndex, 2)[1] : process.env.GUI_BASE_REF) ?? DEFAULT_BASE;
  const current = git(["rev-parse", "--abbrev-ref", "HEAD"]);
  const branch = args[0] ?? current;
  const tab = TAB_BRANCH.exec(branch)?.[1];
  if (!tab) {
    console.log(`owned-paths: "${branch}" is not a tab branch (feat/gui-<tab>); nothing to check.`);
    return 0;
  }
  const map = parseOwnershipMap(readFileSync(ARCHITECTURE, "utf8"));
  const owned = map.tabs[tab];
  if (!owned) {
    console.log(`owned-paths: "${branch}" names no known tab (${Object.keys(map.tabs).join(", ")}); nothing to check.`);
    return 0;
  }
  const ref = resolveRef(branch);
  const files = changedFiles(ref, base, ref === "HEAD" || branch === current);
  const bad = violations(files, tab, map);
  if (bad.length) {
    console.error(`owned-paths: ${branch} touches ${bad.length} path(s) outside the ${tab.toUpperCase()} set:`);
    for (const file of bad)
      console.error(
        `  ${file}${matchesAny(file, map.foundation) ? "   (foundation: report NEEDS_FOUNDATION instead)" : ""}`,
      );
    console.error(`Allowed: ${owned.join(", ")}`);
    return 1;
  }
  console.log(`owned-paths: ${branch} — ${files.length} changed path(s), all inside the ${tab.toUpperCase()} set.`);
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exit(main(process.argv.slice(2)));
}
