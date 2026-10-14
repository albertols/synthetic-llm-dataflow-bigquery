import { createContext, useContext } from "react";

import { DEFAULT_BRAND_ID, type BrandEntry } from "@/brands/types";

import { FALLBACK_HEAD, readBrandHead, themeColorFor } from "./brand";

export type ThemePreference = "dark" | "light" | "system";
export type ResolvedTheme = "dark" | "light";

export type ThemeState = {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
  /** The active brand pack (`data-brand` on <html>); independent of the mode. */
  brand: string;
  /** The brands this build carries, in menu order. */
  brands: readonly BrandEntry[];
  setBrand: (id: string) => void;
};

/** Also read by public/theme-init.js before the first paint (theme.test.ts keeps the two in step). */
export const THEME_STORAGE_KEY = "synthetic-platform.theme";

/** Dark by default; the switcher keeps light (and "system") for readers who need it. */
export const ThemeContext = createContext<ThemeState>({
  preference: "dark",
  resolved: "dark",
  setPreference: () => {},
  brand: DEFAULT_BRAND_ID,
  brands: FALLBACK_HEAD.packs,
  setBrand: () => {},
});

/**
 * The current brand and theme. Re-render on change; charts re-read tokens when
 * `resolved` changes, and AppShell remounts the page when `brand` does.
 */
export function useTheme(): ThemeState {
  return useContext(ThemeContext);
}

export function readStoredPreference(): ThemePreference {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return value === "light" || value === "system" || value === "dark" ? value : "dark";
  } catch {
    return "dark";
  }
}

export function storePreference(preference: ThemePreference): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Private mode or blocked storage: the choice lasts for this page only.
  }
}

export function systemTheme(): ResolvedTheme {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  return preference === "system" ? systemTheme() : preference;
}

/**
 * Stamps `data-brand` and `data-theme` on <html> (tokens.css and the packs'
 * tokens key off them) and the browser colour of that brand and mode.
 */
export function applyAppearance(brand: string, resolved: ResolvedTheme, head = readBrandHead()): void {
  const root = document.documentElement;
  root.dataset.brand = brand;
  root.dataset.theme = resolved;
  root.style.colorScheme = resolved;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", themeColorFor(head, brand, resolved));
}

/** The computed value of a design token (`--chart-1`), for canvas renderers that cannot read CSS. */
export function readToken(name: `--${string}`, fallback = ""): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}
