/**
 * The brand half of the appearance (gui/BRANDING.md, "Brand packs"): which
 * brands this build carries, which one is active, and its browser colour.
 * The build writes the list into index.html (vite-plugins/brandPacks.ts);
 * public/theme-init.js reads the same list before the first paint, and
 * theme.test.ts runs both against each other.
 */
import beamDataflow from "@/brands/beam-dataflow/brand.json";
import { BRAND_HEAD_ID, DEFAULT_BRAND_ID, type BrandEntry, type BrandHead, type Mode } from "@/brands/types";

/** The stored brand, next to the theme's key. Also read by public/theme-init.js. */
export const BRAND_STORAGE_KEY = "synthetic-platform.brand";

/** The committed brand alone: what a page without the build's list (a unit test, say) gets. */
export const FALLBACK_HEAD: BrandHead = {
  default: DEFAULT_BRAND_ID,
  packs: [{ id: beamDataflow.id, name: beamDataflow.name, themeColor: beamDataflow.themeColor }],
};

const isEntry = (value: unknown): value is BrandEntry => {
  const entry = value as BrandEntry | null;
  return (
    typeof entry?.id === "string" &&
    typeof entry.name === "string" &&
    typeof entry.themeColor?.dark === "string" &&
    typeof entry.themeColor.light === "string"
  );
};

/** The build's brand list from its JSON text; anything unreadable is the committed brand alone. */
export function parseBrandHead(text: string | null | undefined): BrandHead {
  if (!text) return FALLBACK_HEAD;
  try {
    const head = JSON.parse(text) as Partial<BrandHead>;
    if (Array.isArray(head.packs) && head.packs.every(isEntry) && head.packs.some((p) => p.id === head.default))
      return { default: head.default!, packs: head.packs };
  } catch {
    // Unreadable: the committed brand.
  }
  return FALLBACK_HEAD;
}

let cached: { text: string | null; head: BrandHead } | null = null;

/** The brands this page carries (index.html's `#brand-packs`). */
export function readBrandHead(): BrandHead {
  if (typeof document === "undefined") return FALLBACK_HEAD;
  const text = document.getElementById(BRAND_HEAD_ID)?.textContent ?? null;
  if (cached?.text !== text) cached = { text, head: parseBrandHead(text) };
  return cached.head;
}

/** The stored brand when this build still carries it, else the build's default (a removed pack falls back). */
export function resolveBrand(stored: string | null, head: BrandHead): string {
  return stored !== null && head.packs.some((p) => p.id === stored) ? stored : head.default;
}

export function readStoredBrand(): string | null {
  try {
    return window.localStorage.getItem(BRAND_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function storeBrand(id: string): void {
  try {
    window.localStorage.setItem(BRAND_STORAGE_KEY, id);
  } catch {
    // Private mode or blocked storage: the choice lasts for this page only.
  }
}

export function brandEntry(head: BrandHead, id: string): BrandEntry {
  return head.packs.find((p) => p.id === id) ?? head.packs.find((p) => p.id === head.default) ?? head.packs[0]!;
}

/** `<meta name="theme-color">` for a brand and mode. */
export function themeColorFor(head: BrandHead, brand: string, mode: Mode): string {
  return brandEntry(head, brand).themeColor[mode];
}
