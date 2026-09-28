/** Lazy chunk: mermaid, initialised with the design tokens and strict security. */
import mermaid from "mermaid";
import { useEffect, useId, useRef, useState } from "react";

import { readToken, useTheme, type ResolvedTheme } from "@/lib/theme";

let initialisedFor: ResolvedTheme | undefined;
let renderCount = 0;

function initialise(theme: ResolvedTheme) {
  if (initialisedFor === theme) return;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "base",
    fontFamily: readToken("--font-ui", "system-ui, sans-serif"),
    themeVariables: {
      darkMode: theme === "dark",
      background: readToken("--surface-1"),
      primaryColor: readToken("--surface-3"),
      primaryTextColor: readToken("--text-1"),
      primaryBorderColor: readToken("--border-strong"),
      secondaryColor: readToken("--surface-2"),
      tertiaryColor: readToken("--surface-1"),
      lineColor: readToken("--text-3"),
      textColor: readToken("--text-2"),
      clusterBkg: readToken("--surface-1"),
      clusterBorder: readToken("--border-strong"),
      edgeLabelBackground: readToken("--surface-2"),
      fontSize: "14px",
    },
  });
  initialisedFor = theme;
}

export default function MermaidRender({ chart }: { chart: string }) {
  const { resolved } = useTheme();
  const reactId = useId();
  const hostRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    initialise(resolved);
    renderCount += 1;
    const id = `mermaid-${reactId.replace(/[^a-zA-Z0-9_-]/g, "")}-${renderCount}`;
    mermaid
      .render(id, chart)
      .then(({ svg }) => {
        if (cancelled || !hostRef.current) return;
        // securityLevel "strict": mermaid sanitises labels and disables click handlers.
        hostRef.current.innerHTML = svg;
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [chart, resolved, reactId]);

  if (error) {
    return (
      <pre className="overflow-x-auto rounded-md border border-status-critical/50 bg-surface-1 p-3 text-xs text-status-critical-text">
        Diagram failed to render: {error}
      </pre>
    );
  }
  return <div ref={hostRef} className="flex justify-center [&_svg]:h-auto [&_svg]:max-w-full" />;
}
