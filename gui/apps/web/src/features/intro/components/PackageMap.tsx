/** The package map: an import-direction diagram (mermaid, lazy) and one card per package with its ADRs. */
import { Package } from "lucide-react";

import { Mermaid } from "@/components/Mermaid";
import { Skeleton } from "@/components/ui/skeleton";
import { repoBlobUrl } from "@/lib/links";

import { PACKAGE_CHART, PACKAGE_CHART_LABEL, PACKAGES } from "../content/packages";
import { AdrChips } from "./HowItWorks";
import { ExternalAnchor, IntroSection, useInViewOnce } from "./primitives";

export function PackageMap() {
  const [ref, seen] = useInViewOnce<HTMLDivElement>();
  return (
    <IntroSection
      id="packages"
      eyebrow="Packages"
      title="Four packages, one import direction"
      concept="intro:import-direction"
      lead="The generator is two Python packages with a strict import rule; the evaluator and this GUI stand apart and meet the generator only through BigQuery tables and generated contracts."
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <figure className="grid content-start gap-2">
          <div ref={ref} className="min-h-72">
            {seen ? (
              <Mermaid chart={PACKAGE_CHART} ariaLabel={PACKAGE_CHART_LABEL} />
            ) : (
              <Skeleton className="h-72 w-full rounded-lg" />
            )}
          </div>
          <figcaption className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-3">
            <span className="inline-flex items-center gap-1.5">
              <svg aria-hidden="true" width="28" height="6">
                <line x1="0" y1="3" x2="28" y2="3" stroke="var(--text-2)" strokeWidth="2" />
              </svg>
              solid: imports
            </span>
            <span className="inline-flex items-center gap-1.5">
              <svg aria-hidden="true" width="28" height="6">
                <line x1="0" y1="3" x2="28" y2="3" stroke="var(--text-2)" strokeWidth="2" strokeDasharray="4 3" />
              </svg>
              dashed: data written or read, contracts generated
            </span>
          </figcaption>
        </figure>
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2" aria-label="Packages">
          {PACKAGES.map((pkg) => (
            <li key={pkg.id} className="flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-surface-1 p-4">
              <h3 className="flex items-center gap-2 font-mono text-sm font-semibold text-text-1">
                <Package className="size-4 shrink-0 text-accent-text" aria-hidden="true" />
                {pkg.id}
              </h3>
              <p className="text-sm leading-snug text-text-2">
                “{pkg.description}”{" "}
                <ExternalAnchor href={repoBlobUrl(pkg.descriptionSource)} className="text-xs">
                  <span className="sr-only">Source: </span>
                  {pkg.descriptionSource.split("/").pop()}
                </ExternalAnchor>
              </p>
              <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-xs">
                <dt className="text-text-3">Imports</dt>
                <dd className="text-text-2">{pkg.imports}</dd>
                <dt className="text-text-3">Runs on</dt>
                <dd className="text-text-2">{pkg.runsOn}</dd>
              </dl>
              <AdrChips numbers={pkg.adrs} className="mt-auto pt-1" />
            </li>
          ))}
        </ul>
      </div>
    </IntroSection>
  );
}
