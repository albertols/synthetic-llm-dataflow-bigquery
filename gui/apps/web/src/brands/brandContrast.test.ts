/**
 * Every brand pack on this machine meets WCAG 2.2 AA where BRANDING.md's
 * tables hold the committed palette to it — computed here, not tabulated, so a
 * deployment's own pack needs no table in the public BRANDING.md. The packs:
 * the committed ones (src/brands/), the test fixture, and any local pack
 * (src/brands/local/), each in both modes: tokens.css's values with the
 * pack's overrides on top, and its top-bar tokens inside the bar.
 *
 *   text ≥ 4.5 : 1    ink, accent text, links, CPU/GPU/Beam labels and status text on
 *                     the surfaces they sit on; accent and link text on their tints;
 *                     the label on an accent button; the bar's ink on the bar
 *   UI   ≥ 3 : 1      input boundaries and the focus ring (WCAG 1.4.11, 2.4.13)
 *   tint ≥ 4.5 : 1    status, CPU, GPU and Beam text on its own 14 % tint (the
 *                     strongest pill and callout tint)
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

import { discoverBrandPacks, packRoots, type BrandPack } from "../../vite-plugins/brandPacks";
import {
  blockColours,
  blockTints,
  contrast,
  over,
  parseTint,
  rgbOf,
  themeBlock,
  type Rgb,
  type Tint,
} from "../styles/contrast";
import { MODES, type Mode } from "./types";

/** apps/web: the nearest ancestor of the working directory holding apps/web/src/styles/tokens.css. */
function webRoot(): string {
  let dir = process.cwd();
  while (!existsSync(join(dir, "apps", "web", "src", "styles", "tokens.css"))) {
    const parent = dirname(dir);
    if (parent === dir) throw new Error("gui/ not found above the working directory");
    dir = parent;
  }
  return join(dir, "apps", "web");
}

const WEB = webRoot();
const TOKENS = readFileSync(join(WEB, "src", "styles", "tokens.css"), "utf8");
const FIXTURES = join(WEB, "src", "test", "fixtures", "brands");

const PACKS: BrandPack[] = discoverBrandPacks([...packRoots(WEB), FIXTURES]);

/** A palette: solid colours and tints by token name. */
type Palette = { colours: Map<string, string>; tints: Map<string, Tint> };

function layer(base: Palette, overrides: Record<string, string>): Palette {
  const colours = new Map(base.colours);
  const tints = new Map(base.tints);
  for (const [name, value] of Object.entries(overrides)) {
    const tint = parseTint(value);
    if (tint) {
      tints.set(name, tint);
      colours.delete(name);
    } else {
      colours.set(name, value);
      tints.delete(name);
    }
  }
  return { colours, tints };
}

const basePalette = (mode: Mode): Palette => {
  const block = themeBlock(TOKENS, mode);
  return { colours: blockColours(block), tints: blockTints(block) };
};

type Check = { label: string; fg: string | Rgb; bg: string | Rgb; min: number };

const STATUS = ["good", "warn", "serious", "critical", "info", "neutral"] as const;
const PILL_TINT = 0.14;

function checksFor(pack: BrandPack, mode: Mode): Check[] {
  const page = layer(basePalette(mode), pack.tokens[mode]);
  const bar = layer(page, pack.nav[mode]);
  const c = (palette: Palette, name: string) => {
    const value = palette.colours.get(name);
    if (!value) throw new Error(`${pack.id} ${mode}: ${name} is not a solid colour`);
    return value;
  };
  const t = (palette: Palette, name: string) => {
    const value = palette.tints.get(name);
    if (!value) throw new Error(`${pack.id} ${mode}: ${name} is not a tint`);
    return value;
  };
  const out: Check[] = [];
  const add = (label: string, fg: string | Rgb, bg: string | Rgb, min: number) => out.push({ label, fg, bg, min });

  const surfaces = ["--bg", "--surface-1", "--surface-2"];
  for (const ink of ["--text-1", "--text-2", "--text-3"])
    for (const surface of [...surfaces, "--surface-3"])
      add(`${ink} on ${surface}`, c(page, ink), c(page, surface), 4.5);
  for (const surface of surfaces) {
    const bg = c(page, surface);
    add(`--accent-text on ${surface}`, c(page, "--accent-text"), bg, 4.5);
    add(
      `--accent-text on --accent-soft over ${surface}`,
      c(page, "--accent-text"),
      over(t(page, "--accent-soft"), bg),
      4.5,
    );
    add(`--link on ${surface}`, c(page, "--link"), bg, 4.5);
    add(`--link on --info-soft over ${surface}`, c(page, "--link"), over(t(page, "--info-soft"), bg), 4.5);
    for (const tone of ["cpu", "gpu", "beam"]) {
      const ink = c(page, `--${tone}-text`);
      add(`--${tone}-text on ${surface}`, ink, bg, 4.5);
      const hue: Tint = { rgb: rgbOf(c(page, `--${tone}`)), alpha: PILL_TINT };
      add(`--${tone}-text on its 14 % tint over ${surface}`, ink, over(hue, bg), 4.5);
    }
    add(`--control-border on ${surface}`, c(page, "--control-border"), bg, 3);
    for (const status of STATUS) {
      const ink = c(page, `--status-${status}-text`);
      const hue: Tint = { rgb: rgbOf(c(page, `--status-${status}`)), alpha: PILL_TINT };
      add(`--status-${status}-text on ${surface}`, ink, bg, 4.5);
      add(`--status-${status}-text on its 14 % tint over ${surface}`, ink, over(hue, bg), 4.5);
    }
  }
  for (const surface of [...surfaces, "--surface-3"])
    add(`--focus-ring on ${surface}`, c(page, "--focus-ring"), c(page, surface), 3);
  add("--accent-fg on --accent", c(page, "--accent-fg"), c(page, "--accent"), 4.5);

  // The top bar: --nav-bg when the pack paints it (a tint is laid over the page), else the page colour.
  const navTint = page.tints.get("--nav-bg");
  const navBg: string | Rgb = navTint
    ? over(navTint, c(page, "--bg"))
    : (page.colours.get("--nav-bg") ?? c(page, "--bg"));
  for (const ink of ["--text-1", "--text-2", "--text-3"]) add(`bar ${ink} on the bar`, c(bar, ink), navBg, 4.5);
  for (const ink of ["--text-1", "--text-2"])
    add(`bar ${ink} on bar --surface-3`, c(bar, ink), c(bar, "--surface-3"), 4.5);
  add("bar --focus-ring on the bar", c(bar, "--focus-ring"), navBg, 3);
  add("bar --link on --info-soft over the bar", c(bar, "--link"), over(t(bar, "--info-soft"), navBg), 4.5);
  add("bar --status-serious-text on bar --surface-3", c(bar, "--status-serious-text"), c(bar, "--surface-3"), 4.5);
  return out;
}

describe("brand packs meet WCAG 2.2 AA (computed)", () => {
  it("finds the committed brand and the fixture", () => {
    expect(PACKS.map((p) => p.id)).toEqual(expect.arrayContaining(["beam-dataflow", "acme"]));
  });

  it("the committed brand overrides nothing: its tokens are tokens.css", () => {
    const beam = PACKS.find((p) => p.id === "beam-dataflow")!;
    expect(beam.tokens).toEqual({ dark: {}, light: {} });
    expect(beam.nav).toEqual({ dark: {}, light: {} });
  });

  const cases = PACKS.flatMap((pack) => MODES.map((mode) => [pack.id, mode, pack] as const));
  it.each(cases)("%s %s", (_id, mode, pack) => {
    const list = checksFor(pack, mode);
    expect(list.length).toBe(94);
    const failures = list
      .filter(({ fg, bg, min }) => contrast(fg, bg) < min)
      .map(({ label, fg, bg, min }) => `${label}: ${contrast(fg, bg).toFixed(2)} < ${min}`);
    expect(failures).toEqual([]);
  });
});
