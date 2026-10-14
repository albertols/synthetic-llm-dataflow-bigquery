import beamMark from "@/assets/marks/beam-logo-3-color-nameless.svg";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { BEAM_URL, DATAFLOW_URL } from "@/lib/links";

/**
 * What the platform is built on, in every brand: the Apache Beam logo and the
 * Dataflow name, each a link to its project (gui/ATTRIBUTION.md: the ASF lets
 * a site show its logos as a link to the project; Google's icon library
 * offers its icons for diagrams, so the banner names Dataflow in text). They
 * identify the technology only: the product's own mark and name stay apart.
 */
export const BUILT_WITH = [
  { href: BEAM_URL, name: "Apache Beam", short: "Apache Beam", tip: "Built with Apache Beam", mark: beamMark },
  {
    href: DATAFLOW_URL,
    name: "Google Cloud Dataflow",
    short: "Dataflow",
    tip: "Runs on Google Cloud Dataflow",
    mark: null,
  },
] as const;

const linkClass =
  "inline-flex h-9 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-text-2 transition-colors hover:bg-surface-3 hover:text-text-1";

/** The "built with" group of the top bar, from 1280 px (below, the "More" menu lists the same links). */
export function BuiltWith({ className }: { className?: string }) {
  const [beam, dataflow] = BUILT_WITH;
  return (
    <div role="group" aria-label="Built with" className={cn("flex items-center", className)}>
      <span aria-hidden="true" className="pr-1 pl-2 text-[11px] text-text-3">
        Built with
      </span>
      <Tooltip content={beam.tip}>
        <a
          href={beam.href}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`${beam.name} (opens in a new tab)`}
          className={linkClass}
        >
          <img src={beam.mark} alt="" width={15} height={20} className="h-5 w-auto" />
          <span aria-hidden="true">{beam.short}</span>
        </a>
      </Tooltip>
      <span aria-hidden="true" className="text-text-3">
        ·
      </span>
      <Tooltip content={dataflow.tip}>
        <a
          href={dataflow.href}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`${dataflow.name} (opens in a new tab)`}
          className={linkClass}
        >
          {dataflow.short}
        </a>
      </Tooltip>
    </div>
  );
}
