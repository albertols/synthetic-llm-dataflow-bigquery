/**
 * Brand packs at build time (gui/BRANDING.md, "Brand packs").
 *
 * Finds every pack folder (`brand.json` plus its logo files) under the
 * committed root, src/brands/, and a deployment's own, src/brands/local/
 * (git-ignored), checks each one, picks the default brand, and writes three
 * things into index.html ahead of public/theme-init.js, so the stored brand
 * and mode are on <html> before the first paint:
 *
 *   - a JSON block (`#brand-packs`) listing the brands: id, name, browser
 *     colour per mode and the logos as data URIs;
 *   - one <style> with each pack's token overrides, keyed on
 *     `:root[data-brand][data-theme]` (and the top bar's re-scoped tokens);
 *   - `data-brand="<default>"` on <html>.
 *
 * With no local pack the output is the committed brand alone. Node-only: the
 * app reads what this writes (src/lib/brand.ts), never this file.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, sep } from "node:path";

import type { Plugin } from "vite";

import {
  BRAND_HEAD_ID,
  DEFAULT_BRAND_ID,
  MODES,
  NAV_TOKENS,
  PAGE_TOKENS,
  type BrandEntry,
  type BrandHead,
  type Mode,
} from "../src/brands/types.ts";

/** One pack as read from disk and checked. Logo paths are absolute. */
export type BrandPack = {
  dir: string;
  id: string;
  name: string;
  isDefault: boolean;
  themeColor: Record<Mode, string>;
  tokens: Record<Mode, Record<string, string>>;
  nav: Record<Mode, Record<string, string>>;
  logo?: { light: string; dark: string; plate?: string; wordmark: boolean };
};

/** The packs a build carries, in menu order (the committed brand first), and its default. */
export type BrandRegistry = { default: string; packs: BrandPack[] };

/** Where the build looks for packs under the web app's root: the committed ones, then a deployment's own. */
export const packRoots = (webRoot: string): string[] => [
  join(webRoot, "src", "brands"),
  join(webRoot, "src", "brands", "local"),
];

const PACK_FILE = "brand.json";
const ID = /^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$/;
const HEX = /^#[0-9a-f]{6}$/;
const RGB_ALPHA = /^rgb\((\d{1,3}) (\d{1,3}) (\d{1,3}) \/ (0|1|0?\.\d+)\)$/;
const LOGO_TYPES: Record<string, string> = { ".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp" };
const MAX_LOGO_BYTES = 64 * 1024;
/** An SVG logo is shown through <img>, where scripts never run; still, a pack that carries one is refused. */
const UNSAFE_SVG = /<script|<foreignObject|\son[a-z]+\s*=|javascript:|(?:xlink:)?href\s*=\s*["']\s*(?:https?:)?\/\//i;
const PACK_KEYS = new Set(["$comment", "id", "name", "default", "themeColor", "logo", "tokens", "nav"]);
const LOGO_KEYS = new Set(["light", "dark", "plate", "wordmark"]);

export class BrandPackError extends Error {
  override name = "BrandPackError";
}

function fail(where: string, message: string): never {
  throw new BrandPackError(`brand pack ${where}: ${message}`);
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** `#rrggbb` or `rgb(r g b / a)`, lower-cased; anything else is refused. */
function colour(where: string, value: unknown): string {
  const text = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (HEX.test(text)) return text;
  const rgb = RGB_ALPHA.exec(text);
  if (rgb && [rgb[1], rgb[2], rgb[3]].every((c) => Number(c) <= 255)) return text;
  return fail(where, `${JSON.stringify(value)} is not a colour (#rrggbb or rgb(r g b / a))`);
}

function hex(where: string, value: unknown): string {
  const text = colour(where, value);
  return HEX.test(text) ? text : fail(where, `${JSON.stringify(value)} must be #rrggbb`);
}

function tokenMaps(where: string, value: unknown, allowed: readonly string[]): Record<Mode, Record<string, string>> {
  const out: Record<Mode, Record<string, string>> = { dark: {}, light: {} };
  if (value === undefined) return out;
  if (!isRecord(value)) return fail(where, "must be an object keyed by mode (dark, light)");
  for (const [mode, map] of Object.entries(value)) {
    if (mode !== "dark" && mode !== "light") fail(where, `unknown mode "${mode}" (dark, light)`);
    if (!isRecord(map)) fail(`${where}.${mode}`, "must map token names to colours");
    for (const [name, raw] of Object.entries(map)) {
      if (!allowed.includes(name)) fail(`${where}.${mode}`, `"${name}" is not a token a pack may set here`);
      out[mode][name] = colour(`${where}.${mode}.${name}`, raw);
    }
  }
  return out;
}

function logoFile(where: string, dir: string, value: unknown): string {
  if (typeof value !== "string" || !value || value.includes("/") || value.includes("\\") || value.startsWith("."))
    return fail(where, "must be a file name inside the pack's folder");
  const path = join(dir, value);
  if (!existsSync(path) || !statSync(path).isFile()) return fail(where, `${value} is not in ${dir}`);
  const type = LOGO_TYPES[extname(value).toLowerCase()];
  if (!type) return fail(where, `${value}: a logo is .svg, .png or .webp`);
  const bytes = statSync(path).size;
  if (bytes > MAX_LOGO_BYTES) fail(where, `${value} is ${bytes} bytes; a logo is at most ${MAX_LOGO_BYTES}`);
  if (type === "image/svg+xml" && UNSAFE_SVG.test(readFileSync(path, "utf8")))
    fail(where, `${value} carries a script, an event handler or an external reference`);
  return path;
}

/** Reads and checks the pack in `dir` (a folder holding brand.json). */
export function readBrandPack(dir: string): BrandPack {
  const file = join(dir, PACK_FILE);
  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(file, "utf8"));
  } catch (error) {
    return fail(file, `not readable JSON (${error instanceof Error ? error.message : String(error)})`);
  }
  if (!isRecord(raw)) return fail(file, "must be a JSON object");
  for (const key of Object.keys(raw)) if (!PACK_KEYS.has(key)) fail(file, `unknown key "${key}"`);

  const folder = dir.split(sep).filter(Boolean).at(-1) ?? "";
  if (typeof raw.id !== "string" || !ID.test(raw.id))
    fail(file, "id must be lower-case letters, digits and hyphens (at most 40)");
  if (raw.id !== folder) fail(file, `id "${raw.id}" must match its folder name "${folder}"`);
  if (typeof raw.name !== "string" || !raw.name.trim() || raw.name.length > 40)
    fail(file, "name must be a non-empty string of at most 40 characters");
  if (raw.default !== undefined && typeof raw.default !== "boolean") fail(file, "default must be true or false");
  if (!isRecord(raw.themeColor)) fail(file, "themeColor must give the browser colour for dark and light");
  const themeColor = {
    dark: hex(`${file} themeColor.dark`, raw.themeColor.dark),
    light: hex(`${file} themeColor.light`, raw.themeColor.light),
  };

  let logo: BrandPack["logo"];
  if (raw.logo !== undefined) {
    if (!isRecord(raw.logo)) fail(file, "logo must be an object { light, dark, plate?, wordmark? }");
    for (const key of Object.keys(raw.logo)) if (!LOGO_KEYS.has(key)) fail(`${file} logo`, `unknown key "${key}"`);
    if (raw.logo.wordmark !== undefined && typeof raw.logo.wordmark !== "boolean")
      fail(`${file} logo.wordmark`, "must be true or false");
    logo = {
      light: logoFile(`${file} logo.light`, dir, raw.logo.light),
      dark: logoFile(`${file} logo.dark`, dir, raw.logo.dark),
      wordmark: raw.logo.wordmark === true,
      ...(raw.logo.plate === undefined ? {} : { plate: hex(`${file} logo.plate`, raw.logo.plate) }),
    };
  }

  return {
    dir,
    id: raw.id,
    name: raw.name.trim(),
    isDefault: raw.default === true,
    themeColor,
    tokens: tokenMaps(`${file} tokens`, raw.tokens, PAGE_TOKENS),
    nav: tokenMaps(`${file} nav`, raw.nav, NAV_TOKENS),
    ...(logo ? { logo } : {}),
  };
}

/** Every pack under `roots`: each sub-folder holding brand.json (a root that does not exist is skipped). */
export function discoverBrandPacks(roots: readonly string[]): BrandPack[] {
  const packs: BrandPack[] = [];
  for (const root of roots) {
    if (!existsSync(root)) continue;
    for (const entry of readdirSync(root, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (!entry.isDirectory() || !existsSync(join(root, entry.name, PACK_FILE))) continue;
      const pack = readBrandPack(join(root, entry.name));
      const twin = packs.find((p) => p.id === pack.id);
      if (twin) fail(pack.dir, `id "${pack.id}" is also used by ${twin.dir}`);
      packs.push(pack);
    }
  }
  return packs;
}

/**
 * The default brand: `override` (VITE_DEFAULT_BRAND) when given, else the one
 * pack marked `"default": true`, else the committed generic brand.
 */
export function brandRegistry(packs: readonly BrandPack[], override?: string): BrandRegistry {
  if (!packs.some((p) => p.id === DEFAULT_BRAND_ID))
    fail(DEFAULT_BRAND_ID, "the committed brand is missing (src/brands/beam-dataflow/brand.json)");
  const ids = packs.map((p) => p.id);
  const marked = packs.filter((p) => p.isDefault);
  let chosen: string;
  if (override) {
    if (!ids.includes(override)) fail(override, `VITE_DEFAULT_BRAND names no brand pack (found: ${ids.join(", ")})`);
    chosen = override;
  } else if (marked.length > 1) {
    return fail(marked.map((p) => p.id).join(", "), 'more than one pack says "default": true');
  } else {
    chosen = marked[0]?.id ?? DEFAULT_BRAND_ID;
  }
  const ordered = [
    ...packs.filter((p) => p.id === DEFAULT_BRAND_ID),
    ...packs.filter((p) => p.id !== DEFAULT_BRAND_ID).sort((a, b) => a.name.localeCompare(b.name)),
  ];
  return { default: chosen, packs: ordered };
}

const dataUri = (path: string) =>
  `data:${LOGO_TYPES[extname(path).toLowerCase()]};base64,${readFileSync(path).toString("base64")}`;

/** What index.html lists for the browser: no paths, logos inlined. */
export function brandHead(registry: BrandRegistry): BrandHead {
  return {
    default: registry.default,
    packs: registry.packs.map((pack): BrandEntry => {
      const entry: BrandEntry = { id: pack.id, name: pack.name, themeColor: pack.themeColor };
      if (pack.logo)
        entry.logo = {
          light: dataUri(pack.logo.light),
          dark: dataUri(pack.logo.dark),
          ...(pack.logo.plate ? { plate: pack.logo.plate } : {}),
          ...(pack.logo.wordmark ? { wordmark: true } : {}),
        };
      return entry;
    }),
  };
}

const declarations = (tokens: Record<string, string>) =>
  Object.entries(tokens)
    .map(([name, value]) => `${name}:${value}`)
    .join(";");

/**
 * The packs' token overrides. `:root[data-brand][data-theme]` outranks
 * tokens.css's `:root[data-theme]`, whatever the order the sheets load in.
 */
export function brandCss(packs: readonly BrandPack[]): string {
  const rules: string[] = [];
  for (const pack of packs)
    for (const mode of MODES) {
      const scope = `:root[data-brand="${pack.id}"][data-theme="${mode}"]`;
      if (Object.keys(pack.tokens[mode]).length) rules.push(`${scope}{${declarations(pack.tokens[mode])}}`);
      if (Object.keys(pack.nav[mode]).length) rules.push(`${scope} [data-app-header]{${declarations(pack.nav[mode])}}`);
    }
  return rules.join("\n");
}

const THEME_INIT = '<script src="/theme-init.js"></script>';

/** index.html with the brand list, the packs' tokens and the default brand written in. */
export function injectBrandHead(html: string, registry: BrandRegistry): string {
  if (!html.includes(THEME_INIT)) fail("index.html", `${THEME_INIT} is missing`);
  const chosen = registry.packs.find((p) => p.id === registry.default)!;
  // `<` escaped, so no value can close the block.
  const json = JSON.stringify(brandHead(registry)).replace(/</g, "\\u003c");
  const css = brandCss(registry.packs);
  const blocks = [
    `<script type="application/json" id="${BRAND_HEAD_ID}">${json}</script>`,
    ...(css ? [`<style id="brand-tokens">\n${css}\n</style>`] : []),
  ];
  return html
    .replace(
      /<html\b([^>]*)>/,
      (_tag, attrs: string) => `<html${attrs.replace(/\sdata-brand="[^"]*"/, "")} data-brand="${registry.default}">`,
    )
    .replace(/(<meta name="theme-color" content=")[^"]*(")/, `$1${chosen.themeColor.dark}$2`)
    .replace(THEME_INIT, `${blocks.join("\n    ")}\n    ${THEME_INIT}`);
}

/**
 * The Vite plugin: index.html gets the brands found on this machine, in dev
 * and in the build. `VITE_DEFAULT_BRAND` (environment or a `.env` file) names
 * the default and wins over a pack's `"default": true`.
 */
export function brandPacksPlugin(): Plugin {
  let roots: string[] = [];
  let override: string | undefined;
  return {
    name: "synthetic-platform:brand-packs",
    configResolved(config) {
      roots = packRoots(config.root);
      const value = (config.env as Record<string, string | undefined>).VITE_DEFAULT_BRAND?.trim();
      override = value ? value : undefined;
    },
    transformIndexHtml: {
      order: "post",
      handler: (html) => injectBrandHead(html, brandRegistry(discoverBrandPacks(roots), override)),
    },
  };
}
