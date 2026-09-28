import { Link } from "@tanstack/react-router";
import { ArrowRight, Workflow } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";

/** Placeholder until the INTRO tab lands (task G1). */
export function IntroPage() {
  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="Intro"
        title="How this project makes synthetic data"
        description="A tour of the pipeline, from a live reference sample to evaluated synthetic tables in BigQuery. Every non-trivial idea carries an (i) with its purpose, formula and sources."
      />
      <EmptyState
        icon={Workflow}
        title="The pipeline tour is on its way"
        description={
          <ul className="mt-1 grid list-disc gap-1 pl-5 text-left">
            <li>
              An animated pipeline: source table → reference sample → source stats → RAG → generation on L4 → Mode A
              guardrails → landing → evaluation.
            </li>
            <li>“How it works” cards for every DESIGN.md section, each with its claim and figure.</li>
            <li>The package map, the relationship shapes gallery and live counters.</li>
            <li>A glossary search over every concept the (i) hints explain.</li>
          </ul>
        }
        action={
          <div className="flex flex-wrap justify-center gap-2">
            <Button asChild variant="primary">
              <Link to="/evaluation">
                Open Evaluation <ArrowRight aria-hidden="true" />
              </Link>
            </Button>
            <Button asChild variant="secondary">
              <Link to="/kit">Browse the design system</Link>
            </Button>
          </div>
        }
      />
    </div>
  );
}
