import { Link } from "@tanstack/react-router";
import { Ellipsis, Gauge, Orbit, SlidersHorizontal, Sparkles, type LucideIcon } from "lucide-react";

import { DataSourceBadge } from "@/components/DataSourceBadge";
import { GITHUB_LINKS, GithubLinks, GithubMark } from "@/components/GithubLinks";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/cn";
import { useDataSource } from "@/lib/dataSource";

import { AppearanceItems, AppearanceMenu } from "./Appearance";
import { BrandLockup } from "./BrandLockup";
import { BUILT_WITH, BuiltWith } from "./BuiltWith";
import { ProductMark } from "./ProductMark";

type TabLink = { to: "/" | "/evaluation" | "/rag" | "/config"; label: string; icon: LucideIcon; exact: boolean };

/** The four tabs, in order. Labels render upper case; the DOM keeps sentence case for screen readers. */
export const TABS: readonly TabLink[] = [
  { to: "/", label: "Intro", icon: Sparkles, exact: true },
  { to: "/evaluation", label: "Evaluation", icon: Gauge, exact: false },
  { to: "/rag", label: "RAG", icon: Orbit, exact: false },
  { to: "/config", label: "Config", icon: SlidersHorizontal, exact: false },
];

/** Below 640 px the brand and theme choices, the GitHub links and the "built with" links fold into one menu so the header stays one row. */
function CompactMenu() {
  return (
    // Non-modal: a modal menu aria-hides the page behind it, which axe reports as aria-hidden-focus.
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="More: brand, theme and links">
          <Ellipsis aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="max-h-(--radix-dropdown-menu-content-available-height) overflow-y-auto">
        <AppearanceItems />
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
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Built with</DropdownMenuLabel>
        {BUILT_WITH.map((item) => (
          <DropdownMenuItem key={item.href} asChild>
            <a href={item.href} target="_blank" rel="noopener noreferrer">
              {item.mark ? (
                <img src={item.mark} alt="" width={12} height={16} className="h-4 w-4 object-contain" />
              ) : (
                <span aria-hidden="true" className="w-4" />
              )}
              <span>
                {item.name}
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
    // data-app-header: a brand pack's top-bar tokens are scoped to it (gui/BRANDING.md, "Brand packs").
    <header
      data-app-header=""
      className="sticky top-0 z-40 border-b border-border bg-bg/85 backdrop-blur-md supports-[backdrop-filter]:bg-bg/70"
    >
      {/* --nav-bg: transparent unless a brand pack paints the bar; then it covers the blurred page colour. */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 bg-(--nav-bg)" />
      <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-3 px-4 md:h-14 md:flex-nowrap md:px-6">
        <Link
          to="/"
          className="flex h-14 shrink-0 items-center gap-2 rounded-md pr-1 text-text-1"
          aria-label="Synthetic Platform — Intro"
        >
          <ProductMark />
          <span className="text-[15px] font-semibold tracking-tight">Synthetic Platform</span>
        </Link>
        <BrandLockup />

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
          <BuiltWith className="mr-1 hidden border-r border-border pr-2 sm:flex" />
          <DataSourceBadge source={source} />
          <div className="hidden items-center gap-1 sm:flex">
            <AppearanceMenu />
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
