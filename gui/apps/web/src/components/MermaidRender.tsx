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

export default function MermaidRender({
  chart,
  ariaLabel,
  onError,
}: {
  chart: string;
  /** What the diagram shows; repeated in the error text so a failed render still says it. */
  ariaLabel: string;
  /** The render error (null once a render succeeds), so the frame can stop being an image. */
  onError?: (message: string | null) => void;
}) {
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
        onError?.(null);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        const message = reason instanceof Error ? reason.message : String(reason);
        setError(message);
        onError?.(message);
      });
    return () => {
      cancelled = true;
    };
  }, [chart, resolved, reactId, onError]);

  if (error) {
    // Not inside role="img" any more (Mermaid drops the role on error): screen readers read this.
    return (
      <div className="grid gap-1 rounded-md border border-status-critical/50 bg-surface-1 p-3 text-xs">
        <p className="text-text-2">
          Diagram not shown: <span className="text-text-1">{ariaLabel}</span>
        </p>
        <pre className="overflow-x-auto whitespace-pre-wrap text-status-critical-text">
          Diagram failed to render: {error}
        </pre>
      </div>
    );
  }
  return <div ref={hostRef} className="flex justify-center [&_svg]:h-auto [&_svg]:max-w-full" />;
}
