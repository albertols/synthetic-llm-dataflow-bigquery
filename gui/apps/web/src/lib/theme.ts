import { createContext, useContext } from "react";

export type ThemePreference = "dark" | "light" | "system";
export type ResolvedTheme = "dark" | "light";

export type ThemeState = {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
};

/** Also read by public/theme-init.js before the first paint (theme.test.ts keeps the two in step). */
export const THEME_STORAGE_KEY = "synthetic-platform.theme";
/** The browser UI colour per theme (`<meta name="theme-color">`); also in public/theme-init.js. */
export const THEME_COLOR: Record<ResolvedTheme, string> = { dark: "#0b0d12", light: "#f4f5f7" };

/** Dark by default; the toggle keeps light (and "system") for readers who need it. */
export const ThemeContext = createContext<ThemeState>({
  preference: "dark",
  resolved: "dark",
  setPreference: () => {},
});

/** The current theme. Re-render on change; charts re-read tokens when `resolved` changes. */
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

/** Stamps `data-theme` on <html> (tokens.css keys off it) and the browser theme colour. */
export function applyTheme(resolved: ResolvedTheme): void {
  const root = document.documentElement;
  root.dataset.theme = resolved;
  root.style.colorScheme = resolved;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[resolved]);
}

/** The computed value of a design token (`--chart-1`), for canvas renderers that cannot read CSS. */
export function readToken(name: `--${string}`, fallback = ""): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}
