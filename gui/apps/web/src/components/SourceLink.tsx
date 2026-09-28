import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { parseSource, repoBlobUrl } from "@/lib/links";

export type SourceLinkProps = {
  /** "path/to/file.py:42" (the knobs.json `source` format), or use `path` + `line`. */
  source?: string;
  path?: string;
  line?: number;
  /** Branch or tag; defaults to the repo's default branch. */
  gitRef?: string;
  /** Link text; defaults to "path:line" in monospace. */
  children?: ReactNode;
  className?: string;
};

/** A link to the code on GitHub: the GUI's "truth over docs" citation. Opens in a new tab. */
export function SourceLink({ source, path, line, gitRef, children, className }: SourceLinkProps) {
  const parsed = source ? parseSource(source) : { path: path ?? "", line };
  const label = parsed.line ? `${parsed.path}:${parsed.line}` : parsed.path;
  return (
    <a
      href={repoBlobUrl(parsed.path, parsed.line, gitRef)}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded-sm text-link hover:underline",
        !children && "font-mono text-xs",
        className,
      )}
    >
      <span className="truncate">{children ?? label}</span>
      <ExternalLink className="size-3 shrink-0" aria-hidden="true" />
      <span className="sr-only"> (source on GitHub, opens in a new tab)</span>
    </a>
  );
}
