/**
 * One source for contrast: tokens.css holds the colours, gui/BRANDING.md the
 * measured ratios, and this test recomputes every ratio in BRANDING.md's
 * palette tables from tokens.css (WCAG 2.x relative luminance) — so a token
 * change that moves a ratio fails here until the table says so.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

/** The gui/ directory: the nearest ancestor of the working directory holding apps/web/src/styles/tokens.css. */
function guiRoot(): string {
  let dir = process.cwd();
  while (!existsSync(join(dir, "apps", "web", "src", "styles", "tokens.css"))) {
    const parent = dirname(dir);
    if (parent === dir) throw new Error("gui/ not found above the working directory");
    dir = parent;
  }
  return dir;
}

const GUI = guiRoot();
const TOKENS = readFileSync(join(GUI, "apps/web/src/styles/tokens.css"), "utf8");
const BRANDING = readFileSync(join(GUI, "BRANDING.md"), "utf8");

type Theme = "dark" | "light";

/** `--name: #hex;` pairs of one theme block. */
function themeTokens(theme: Theme): Map<string, string> {
  const start =
    theme === "dark" ? TOKENS.indexOf(':root[data-theme="dark"] {') : TOKENS.indexOf(':root[data-theme="light"] {');
  const block = TOKENS.slice(start, TOKENS.indexOf("\n}", start));
  return new Map([...block.matchAll(/(--[\w-]+):\s*(#[0-9a-f]{6})\b/gi)].map((m) => [m[1]!, m[2]!.toLowerCase()]));
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

/** Rows of the markdown table under `heading`, as trimmed cells. */
function tableRows(heading: string): string[][] {
  const from = BRANDING.indexOf(heading);
  expect(from, heading).toBeGreaterThan(-1);
  const lines = BRANDING.slice(from).split("\n").slice(1);
  const start = lines.findIndex((l) => l.startsWith("|"));
  const rows: string[][] = [];
  for (const line of lines.slice(start + 2)) {
    if (!line.startsWith("|")) break;
    rows.push(
      line
        .split("|")
        .slice(1, -1)
        .map((c) => c.trim()),
    );
  }
  return rows;
}

const STATUS_TEXT: Record<string, string> = {
  good: "--status-good-text",
  warn: "--status-warn-text",
  serious: "--status-serious-text",
  critical: "--status-critical-text",
};

/** "`--text-1/2/3`" → three tokens; "status text good / warn …" → the status text tokens. */
function tokenNames(cell: string): string[] {
  if (cell.startsWith("status text"))
    return cell
      .replace("status text", "")
      .split("/")
      .map((s) => STATUS_TEXT[s.trim()]!);
  const name = /`(--[\w-]+?)(?:\/(\d(?:\/\d)*))?`/.exec(cell);
  if (!name) return [];
  if (!name[2]) return [name[1]!];
  const stem = name[1]!.replace(/\d$/, "");
  return [name[1]!, ...name[2].split("/").map((n) => `${stem}${n}`)];
}

type Check = { label: string; stated: number; fg: string; bg: string };

function checks(theme: Theme, heading: string): Check[] {
  const tokens = themeTokens(theme);
  const surface = tokens.get("--surface-1")!;
  const out: Check[] = [];
  for (const [tokenCell, hexCell, ratioCell] of tableRows(heading)) {
    const names = tokenNames(tokenCell!);
    const hexes = [...hexCell!.matchAll(/#[0-9a-f]{6}/gi)].map((m) => m[0].toLowerCase());
    // The table's hex column must be the token's value: the palette is typed once, in tokens.css.
    names.forEach((name, i) => expect(hexes[i], `${theme} ${name}`).toBe(tokens.get(name)));
    if (!ratioCell || ratioCell === "—") continue;
    const onToken = /`(--[\w-]+)` on `(--[\w-]+)`/.exec(tokenCell!);
    if (onToken) {
      out.push({
        label: tokenCell!,
        stated: Number(ratioCell),
        fg: tokens.get(onToken[1]!)!,
        bg: tokens.get(onToken[2]!)!,
      });
      continue;
    }
    const [main, extra] = ratioCell.split("(");
    main!
      .split("/")
      .map((v) => Number(v.trim()))
      .forEach((stated, i) => out.push({ label: names[i]!, stated, fg: tokens.get(names[i]!)!, bg: surface }));
    const on = extra ? /([\d.]+) on (surface-\d)/.exec(extra) : null;
    if (on)
      out.push({
        label: `${names[0]} on ${on[2]}`,
        stated: Number(on[1]),
        fg: tokens.get(names[0]!)!,
        bg: tokens.get(`--${on[2]}`)!,
      });
  }
  return out;
}

describe("BRANDING.md contrast table", () => {
  it.each([
    ["dark", "### Dark (default)"],
    ["light", "### Light (accessibility toggle)"],
  ] as const)("%s ratios are what tokens.css computes to", (theme, heading) => {
    const list = checks(theme, heading);
    expect(list.length).toBeGreaterThan(8);
    for (const { label, stated, fg, bg } of list) {
      expect(Number.isFinite(stated), label).toBe(true);
      // BRANDING.md prints one decimal: the computed ratio rounded the same way.
      expect(
        Math.round(contrast(fg, bg) * 10) / 10,
        `${theme} ${label} (computed ${contrast(fg, bg).toFixed(3)})`,
      ).toBe(stated);
    }
  });

  it("tokens.css points at BRANDING.md instead of repeating the ratios", () => {
    const header = TOKENS.slice(0, TOKENS.indexOf("*/"));
    expect(header).toContain("BRANDING.md");
    expect(header).not.toMatch(/\d+\.\d:1/);
  });
});
