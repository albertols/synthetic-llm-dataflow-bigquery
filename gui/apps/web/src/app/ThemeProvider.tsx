import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { readBrandHead, readStoredBrand, resolveBrand, storeBrand } from "@/lib/brand";
import {
  applyAppearance,
  readStoredPreference,
  resolveTheme,
  storePreference,
  systemTheme,
  ThemeContext,
  type ResolvedTheme,
  type ThemePreference,
} from "@/lib/theme";

/**
 * Owns the appearance: a brand (the build's default unless the reader picked
 * another) and a mode (dark by default; light or "system" on request), both
 * stored per browser. The stamps on <html> are updated synchronously in the
 * setters, before children re-render, so canvas renderers that read tokens
 * (ECharts, deck.gl, mermaid) see the new values on their next effect.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [head] = useState(readBrandHead);
  const [brand, setBrandState] = useState(() => resolveBrand(readStoredBrand(), head));
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference);
  const [system, setSystem] = useState<ResolvedTheme>(systemTheme);
  const resolved: ResolvedTheme = preference === "system" ? system : preference;

  useEffect(() => {
    if (preference !== "system") return undefined;
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => {
      const next = systemTheme();
      applyAppearance(brand, next, head);
      setSystem(next);
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [preference, brand, head]);

  const setPreference = useCallback(
    (next: ThemePreference) => {
      storePreference(next);
      applyAppearance(brand, resolveTheme(next), head);
      setSystem(systemTheme());
      setPreferenceState(next);
    },
    [brand, head],
  );

  const setBrand = useCallback(
    (id: string) => {
      const next = resolveBrand(id, head);
      storeBrand(next);
      applyAppearance(next, resolved, head);
      setBrandState(next);
    },
    [head, resolved],
  );

  const value = useMemo(
    () => ({ preference, resolved, setPreference, brand, brands: head.packs, setBrand }),
    [preference, resolved, setPreference, brand, head, setBrand],
  );
  return <ThemeContext value={value}>{children}</ThemeContext>;
}
