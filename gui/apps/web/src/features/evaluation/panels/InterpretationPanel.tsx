/**
 * Interpretation: the run in a headline, then ranked sentences built from
 * each metric's catalogue interpretation, status, noise floor and baseline.
 * Each sentence opens the view that holds its evidence.
 */
import { ArrowRight, Lightbulb } from "lucide-react";
import { useState } from "react";

import { InfoHint } from "@/components/InfoHint";
import { LevelChip } from "@/components/LevelChip";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";

import { isLevel } from "../lib/catalogue";
import type { Finding } from "../lib/interpret";
import type { OpenTarget } from "./Scorecards";

const TAB_LABEL: Record<string, string> = {
  overview: "overview",
  columns: "column drawer",
  pairs: "pairs",
  privacy: "privacy",
  detection: "detection",
  relational: "relational",
  params: "parameters",
};

export function InterpretationPanel({
  headline,
  findings,
  onOpen,
  initial = 6,
}: {
  headline: string;
  findings: Finding[];
  onOpen: (target: OpenTarget) => void;
  initial?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? findings : findings.slice(0, initial);
  return (
    <section
      aria-labelledby="interpretation-title"
      className="grid gap-3 rounded-lg border border-border bg-surface-1 p-4 md:p-5"
    >
      <div className="flex items-center gap-2">
        <Lightbulb className="size-4 text-accent-text" aria-hidden="true" />
        <h2
          id="interpretation-title"
          className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1"
        >
          Interpretation
          <InfoHint concept="eval:interpretation" />
        </h2>
      </div>
      <p className="max-w-4xl text-sm leading-relaxed text-text-1">
        <span data-testid="run-headline">{headline}</span>
        <InfoHint concept="eval:info-status" />
      </p>
      {findings.length ? (
        <>
          <ol className="grid gap-2" aria-label="Findings, most severe first">
            {shown.map((finding) => (
              <li
                key={finding.key}
                data-severity={finding.severity}
                className="grid gap-1.5 rounded-md border border-border bg-surface-2/40 p-3 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-start sm:gap-3"
              >
                <div className="flex items-center gap-1.5">
                  <StatusPill status={finding.severity === "info" ? "info" : finding.severity} size="sm" />
                  {isLevel(finding.level) ? <LevelChip level={finding.level} size="sm" /> : null}
                </div>
                <p className="text-sm leading-relaxed text-text-2">{finding.text}</p>
                <Button variant="ghost" size="sm" onClick={() => onOpen(finding.target)} className="justify-self-start">
                  Open {TAB_LABEL[finding.target.tab] ?? finding.target.tab}
                  <ArrowRight aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ol>
          {findings.length > initial ? (
            <Button variant="secondary" size="sm" className="justify-self-start" onClick={() => setExpanded((v) => !v)}>
              {expanded ? "Show fewer" : `Show all ${findings.length} findings`}
            </Button>
          ) : null}
        </>
      ) : (
        <p className="text-sm text-text-2">No metric failed, warned or was skipped.</p>
      )}
    </section>
  );
}
