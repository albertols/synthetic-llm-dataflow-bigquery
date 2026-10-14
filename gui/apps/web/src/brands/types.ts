/**
 * Brand packs (gui/BRANDING.md, "Brand packs"): the names a pack may set and
 * the shape the build writes into index.html for the browser.
 *
 * A pack is a folder holding `brand.json` (and its logo files). The build
 * finds the committed packs in src/brands/<id>/ and a deployment's own packs
 * in src/brands/local/<id>/ (git-ignored); vite-plugins/brandPacks.ts reads
 * and checks them. Brand and mode are independent: <html> carries
 * `data-brand` and `data-theme`.
 */

export type Mode = "dark" | "light";
export const MODES: readonly Mode[] = ["dark", "light"];

/** The committed generic brand: its tokens are tokens.css itself, unchanged. */
export const DEFAULT_BRAND_ID = "beam-dataflow";

/** The id of the JSON block in index.html that lists the build's brands (read by public/theme-init.js too). */
export const BRAND_HEAD_ID = "brand-packs";

/**
 * Tokens a pack may override for the page, per mode. Surfaces, ink, accent,
 * link, focus, the text variants that must keep their contrast on the pack's
 * surfaces, the chart frame and the top bar. Never the chart slots, the
 * sequential and diverging ramps or the status hues: those are validated once
 * and shared by every brand.
 */
export const PAGE_TOKENS = [
  "--bg",
  "--surface-1",
  "--surface-2",
  "--surface-3",
  "--border",
  "--border-strong",
  "--control-border",
  "--overlay",
  "--text-1",
  "--text-2",
  "--text-3",
  "--accent",
  "--accent-hover",
  "--accent-fg",
  "--accent-text",
  "--accent-soft",
  "--link",
  "--info-soft",
  "--focus-ring",
  "--cpu-text",
  "--gpu-text",
  "--status-good-text",
  "--status-warn-text",
  "--status-serious-text",
  "--status-critical-text",
  "--status-info-text",
  "--status-neutral-text",
  "--chart-surface",
  "--chart-grid",
  "--chart-axis",
  "--div-mid",
  "--nav-bg",
] as const;

/**
 * Tokens a pack may re-scope inside the top bar (`[data-app-header]`), per
 * mode: a bar painted in the brand's colour (`--nav-bg`) needs its own ink,
 * hover surface, accent and focus ring.
 */
export const NAV_TOKENS = [
  "--surface-1",
  "--surface-2",
  "--surface-3",
  "--border",
  "--border-strong",
  "--control-border",
  "--text-1",
  "--text-2",
  "--text-3",
  "--accent",
  "--accent-hover",
  "--accent-fg",
  "--accent-text",
  "--accent-soft",
  "--link",
  "--info-soft",
  "--focus-ring",
  "--status-serious-text",
] as const;

export type PageToken = (typeof PAGE_TOKENS)[number];
export type NavToken = (typeof NAV_TOKENS)[number];

/** A pack's logo as the browser gets it: data URIs, inlined by the build. */
export type BrandLogo = {
  light: string;
  dark: string;
  /** A backing plate behind the logo, for a mark drawn for a light background. */
  plate?: string;
  /** The logo already spells the brand's name: the banner prints no name beside it. */
  wordmark?: boolean;
};

/** One brand, as index.html lists it. */
export type BrandEntry = {
  id: string;
  name: string;
  /** `<meta name="theme-color">` per mode. */
  themeColor: Record<Mode, string>;
  logo?: BrandLogo;
};

/** The JSON block in index.html: the build's brands and its default. */
export type BrandHead = { default: string; packs: BrandEntry[] };
