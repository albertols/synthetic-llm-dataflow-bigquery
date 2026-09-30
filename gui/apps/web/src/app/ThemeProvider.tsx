import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  applyTheme,
  readStoredPreference,
  resolveTheme,
  storePreference,
  systemTheme,
  ThemeContext,
  type ResolvedTheme,
  type ThemePreference,
} from "@/lib/theme";

/**
 * Owns the theme: dark by default, light (or "system") on request, stored
 * per browser. The `data-theme` stamp on <html> is updated synchronously in
 * `setPreference`, before children re-render, so canvas renderers that read
 * tokens (ECharts, deck.gl, mermaid) see the new values on their next effect.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference);
  const [system, setSystem] = useState<ResolvedTheme>(systemTheme);

  useEffect(() => {
    if (preference !== "system") return undefined;
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => {
      const next = systemTheme();
      applyTheme(next);
      setSystem(next);
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [preference]);

  const setPreference = useCallback((next: ThemePreference) => {
    storePreference(next);
    applyTheme(resolveTheme(next));
    setSystem(systemTheme());
    setPreferenceState(next);
  }, []);

  const resolved: ResolvedTheme = preference === "system" ? system : preference;
  const value = useMemo(() => ({ preference, resolved, setPreference }), [preference, resolved, setPreference]);
  return <ThemeContext value={value}>{children}</ThemeContext>;
}
