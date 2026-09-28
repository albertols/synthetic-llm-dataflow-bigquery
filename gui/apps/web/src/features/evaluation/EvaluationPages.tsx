import { getRouteApi, Link } from "@tanstack/react-router";
import { GitCompareArrows, Gauge, ScanSearch } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { LEVEL_ORDER, LevelChip } from "@/components/LevelChip";
import { PageHeader } from "@/components/PageHeader";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const METRIC_STATUSES = ["pass", "warn", "fail", "info", "not_evaluated"] as const;
const RUN_STATUSES = ["RUNNING", "SUCCEEDED", "SUCCEEDED_WITH_WARNINGS", "PARTIAL", "SKIPPED", "FAILED"] as const;

function Vocabulary() {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-1">
          <CardTitle>Reading an evaluation</CardTitle>
          <InfoHint concept="core:status" />
        </div>
        <CardDescription>
          The words every view here uses: statuses, run states and the seven catalogue levels.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-3">
        <section aria-labelledby="vocab-metric" className="grid content-start gap-2">
          <h3 id="vocab-metric" className="text-xs font-semibold text-text-3">
            Metric status
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {METRIC_STATUSES.map((status) => (
              <StatusPill key={status} status={status} />
            ))}
          </div>
        </section>
        <section aria-labelledby="vocab-run" className="grid content-start gap-2">
          <h3 id="vocab-run" className="text-xs font-semibold text-text-3">
            Run status
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {RUN_STATUSES.map((status) => (
              <StatusPill key={status} status={status} />
            ))}
          </div>
        </section>
        <section aria-labelledby="vocab-level" className="grid content-start gap-2">
          <h3 id="vocab-level" className="text-xs font-semibold text-text-3">
            Levels
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {LEVEL_ORDER.map((level) => (
              <LevelChip key={level} level={level} withHint />
            ))}
          </div>
        </section>
      </CardContent>
    </Card>
  );
}

/** Placeholder until the EVALUATION tab lands (task G2). */
export function EvaluationListPage() {
  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="Evaluation"
        title="Evaluations"
        concept="core:score"
        description="Every evaluation run: scores per family, the noise floor behind each metric, and side-by-side comparison of runs."
      />
      <Vocabulary />
      <EmptyState
        icon={Gauge}
        title="The evaluations table is on its way"
        description={
          <ul className="mt-1 grid list-disc gap-1 pl-5 text-left">
            <li>Every evaluation with status, trigger, runner and scores, filtered through the URL.</li>
            <li>
              A run view: scorecards per family and level, the model graph, a column × metric heatmap and column
              drawers.
            </li>
            <li>
              A compare view: trends with noise-floor bands, a fidelity-vs-privacy Pareto and a noise-aware A/B diff.
            </li>
          </ul>
        }
        action={
          <Button asChild variant="secondary">
            <Link to="/evaluation/compare" search={{ ids: [] }}>
              <GitCompareArrows aria-hidden="true" />
              Open compare
            </Link>
          </Button>
        }
      />
    </div>
  );
}

const runRoute = getRouteApi("/evaluation/$evaluationId");

export function EvaluationRunPage() {
  const { evaluationId } = runRoute.useParams();
  return (
    <div className="flex flex-col gap-8">
      <PageHeader eyebrow="Evaluation" title={`Evaluation ${evaluationId}`} />
      <EmptyState
        icon={ScanSearch}
        title="The run view is on its way"
        description="Scorecards, the model graph, the column × metric heatmap, privacy, detection and relational panels."
      />
    </div>
  );
}

const compareRoute = getRouteApi("/evaluation/compare");

export function EvaluationComparePage() {
  const { ids } = compareRoute.useSearch();
  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="Evaluation"
        title="Compare evaluations"
        description={ids.length ? `Selected: ${ids.join(", ")}` : "Pick two or more evaluations from the list."}
      />
      <EmptyState
        icon={GitCompareArrows}
        title="The compare view is on its way"
        description="Trend small multiples, a run × metric heatmap, a radar of family scores and a noise-aware A/B diff."
      />
    </div>
  );
}
