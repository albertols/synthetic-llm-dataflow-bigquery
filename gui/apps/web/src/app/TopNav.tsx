import { Link } from "@tanstack/react-router";
import { Ellipsis, Gauge, Moon, Orbit, SlidersHorizontal, Sparkles, Sun, type LucideIcon } from "lucide-react";

import { DataSourceBadge } from "@/components/DataSourceBadge";
import { GITHUB_LINKS, GithubLinks, GithubMark } from "@/components/GithubLinks";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { useDataSource } from "@/lib/dataSource";
import { useTheme } from "@/lib/theme";

import { ProductMark } from "./ProductMark";

type TabLink = { to: "/" | "/evaluation" | "/rag" | "/config"; label: string; icon: LucideIcon; exact: boolean };

/** The four tabs, in order. Labels render upper case; the DOM keeps sentence case for screen readers. */
export const TABS: readonly TabLink[] = [
  { to: "/", label: "Intro", icon: Sparkles, exact: true },
  { to: "/evaluation", label: "Evaluation", icon: Gauge, exact: false },
  { to: "/rag", label: "RAG", icon: Orbit, exact: false },
  { to: "/config", label: "Config", icon: SlidersHorizontal, exact: false },
];

function useThemeSwitch() {
  const { resolved, setPreference } = useTheme();
  const next = resolved === "dark" ? "light" : "dark";
  return {
    label: `Switch to ${next} theme`,
    Icon: resolved === "dark" ? Sun : Moon,
    toggle: () => setPreference(next),
  };
}

function ThemeToggle() {
  const { label, Icon, toggle } = useThemeSwitch();
  return (
    <Tooltip content={label}>
      <Button variant="ghost" size="icon" aria-label={label} onClick={toggle}>
        <Icon aria-hidden="true" />
      </Button>
    </Tooltip>
  );
}

/** Below 640 px the theme switch and the GitHub links fold into one menu so the header stays one row. */
function CompactMenu() {
  const { label, Icon, toggle } = useThemeSwitch();
  return (
    // Non-modal: a modal menu aria-hides the page behind it, which axe reports as aria-hidden-focus.
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="More: theme and GitHub links">
          <Ellipsis aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuItem onSelect={toggle}>
          <Icon aria-hidden="true" />
          {label}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        {GITHUB_LINKS.map((link) => (
          <DropdownMenuItem key={link.href} asChild>
            <a href={link.href} target="_blank" rel="noopener noreferrer">
              <GithubMark className="size-4 text-text-3" />
              <span>
                {link.name}
                <span className="sr-only"> (opens in a new tab)</span>
              </span>
            </a>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function TopNav() {
  const source = useDataSource();
  return (
    <header className="sticky top-0 z-40 border-b border-border bg-bg/85 backdrop-blur-md supports-[backdrop-filter]:bg-bg/70">
      <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-3 px-4 md:h-14 md:flex-nowrap md:px-6">
        <Link
          to="/"
          className="flex h-14 shrink-0 items-center gap-2 rounded-md pr-1 text-text-1"
          aria-label="Synthetic Platform — Intro"
        >
          <ProductMark />
          <span className="text-[15px] font-semibold tracking-tight">Synthetic Platform</span>
        </Link>

        <nav
          aria-label="Tabs"
          className="order-last -mx-4 w-[calc(100%+2rem)] overflow-x-auto border-t border-border px-2 md:order-none md:mx-0 md:ml-4 md:w-auto md:border-0 md:px-0"
        >
          <ul className="flex w-full min-w-max items-center gap-1 md:w-auto md:gap-0.5">
            {TABS.map(({ to, label, icon: Icon, exact }) => (
              <li key={to} className="flex-1 md:flex-none">
                <Link
                  to={to}
                  activeOptions={{ exact, includeSearch: false }}
                  activeProps={{ "aria-current": "page", "data-active": "true" }}
                  className={cn(
                    "group relative flex h-11 items-center justify-center gap-2 rounded-md px-3 font-mono text-xs font-semibold tracking-[0.12em] text-text-2 uppercase transition-colors md:h-9",
                    "hover:bg-surface-3 hover:text-text-1",
                    "data-[active=true]:text-text-1",
                    "after:absolute after:inset-x-3 after:-bottom-px after:h-0.5 after:rounded-full after:bg-accent after:opacity-0 data-[active=true]:after:opacity-100 md:after:-bottom-[11px]",
                  )}
                >
                  <Icon
                    className="hidden size-4 text-text-3 group-data-[active=true]:text-accent-text sm:block"
                    aria-hidden="true"
                  />
                  {label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <div className="ml-auto flex items-center gap-1">
          <DataSourceBadge source={source} />
          <div className="hidden items-center gap-1 sm:flex">
            <ThemeToggle />
            <GithubLinks />
          </div>
          <div className="sm:hidden">
            <CompactMenu />
          </div>
        </div>
      </div>
    </header>
  );
}
