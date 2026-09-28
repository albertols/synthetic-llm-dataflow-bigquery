/**
 * EVALUATION's addition to every metric (i): "Related knob" links to the CONFIG
 * knob sheet (lib/relatedKnobs.ts says which knob and why). Provided once per
 * page, so the (i) of a ChartFrame, a scorecard or a heatmap cell all get it.
 */
import { Link } from "@tanstack/react-router";
import { SlidersHorizontal } from "lucide-react";
import type { ReactNode } from "react";

import { ConceptExtrasContext } from "@/lib/conceptExtras";

import { relatedKnobs } from "../lib/relatedKnobs";

const METRIC = "metric:";

function relatedKnobLinks(conceptId: string): ReactNode {
  if (!conceptId.startsWith(METRIC)) return null;
  const related = relatedKnobs(conceptId.slice(METRIC.length));
  if (!related.length) return null;
  return (
    <div className="grid gap-1.5 border-t border-border pt-3" data-testid="related-knobs">
      <p className="text-xs font-semibold text-text-2">{related.length > 1 ? "Related knobs" : "Related knob"}</p>
      <ul className="grid gap-1.5">
        {related.map(({ knob, why }) => (
          <li key={knob} className="grid gap-0.5">
            <Link
              to="/config"
              search={{ section: "amp", knob }}
              className="inline-flex items-center gap-1.5 self-start rounded-sm px-1 py-0.5 text-sm text-link hover:bg-surface-3 hover:underline"
            >
              <SlidersHorizontal className="size-3.5 shrink-0 text-text-3" aria-hidden="true" />
              <code className="font-mono">{knob}</code>
              <span className="sr-only"> — open its sheet in Config</span>
            </Link>
            <p className="px-1 text-xs leading-snug text-text-3">{why}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Wraps an EVALUATION page: its metric (i) popovers gain the related-knob links. */
export function RelatedKnobsProvider({ children }: { children: ReactNode }) {
  return <ConceptExtrasContext value={relatedKnobLinks}>{children}</ConceptExtrasContext>;
}
