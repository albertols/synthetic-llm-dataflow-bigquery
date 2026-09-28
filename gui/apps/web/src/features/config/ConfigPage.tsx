import { SlidersHorizontal } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";

/** Placeholder until the CONFIG tab lands (task G4). */
export function ConfigPage() {
  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="Config"
        title="Knobs, scenarios and source statistics"
        description="Every setting the pipeline reads, with its value from the code, what turning it does, and what a 90M-row run from a 10k sample implies."
      />
      <EmptyState
        icon={SlidersHorizontal}
        title="The knob console is on its way"
        description={
          <ul className="mt-1 grid list-disc gap-1 pl-5 text-left">
            <li>
              A console of text-labelled knobs per channel, values exported from the code with a link to the line.
            </li>
            <li>
              A scenario calculator: DKW bands, rare-category capture, tail points, pool reuse and collision odds.
            </li>
            <li>The source-stats explorer (sample vs exact tier) and the guardrails explainer.</li>
          </ul>
        }
      />
    </div>
  );
}
