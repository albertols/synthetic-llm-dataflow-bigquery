import { cn } from "@/lib/cn";
import { DSG_URL, REPO_URL } from "@/lib/links";

import { Tooltip } from "./ui/tooltip";

/** The GitHub mark (Octicons "mark-github", MIT; GitHub logo used only to link to GitHub — see ATTRIBUTION.md). */
export function GithubMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" className={className} fill="currentColor">
      <path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z" />
    </svg>
  );
}

export const GITHUB_LINKS = [
  { href: REPO_URL, short: "Repo", name: "Repo: synthetic-llm-dataflow-bigquery on GitHub" },
  { href: DSG_URL, short: "DSG", name: "DSG: Dataflow Solution Guides on GitHub" },
] as const;

/** The two GitHub links in the top nav: this project and the Dataflow Solution Guides. */
export function GithubLinks({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-0.5", className)}>
      {GITHUB_LINKS.map((link) => (
        <Tooltip key={link.href} content={link.name}>
          <a
            href={link.href}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`${link.name} (opens in a new tab)`}
            className="inline-flex h-9 min-w-9 items-center justify-center gap-1.5 rounded-md px-2 text-sm text-text-2 transition-colors hover:bg-surface-3 hover:text-text-1"
          >
            <GithubMark className="size-4" />
            <span className="hidden text-xs font-medium lg:inline" aria-hidden="true">
              {link.short}
            </span>
          </a>
        </Tooltip>
      ))}
    </div>
  );
}
