import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

import { Toaster } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { loadConcepts } from "@/lib/concepts";

import { TopNav } from "./TopNav";

const PRODUCT = "Synthetic Platform";

/** Sets the document title per route and, after client-side navigation, announces it and moves focus to <main>. */
function RouteAnnouncer() {
  const title = useRouterState({ select: (state) => state.matches.at(-1)?.staticData.title ?? "Page not found" });
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const liveRef = useRef<HTMLParagraphElement>(null);
  const firstRender = useRef(true);

  useEffect(() => {
    document.title = `${title} · ${PRODUCT}`;
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    if (liveRef.current) liveRef.current.textContent = `${title} page`;
    document.getElementById("main")?.focus({ preventScroll: true });
  }, [pathname, title]);

  return <p ref={liveRef} aria-live="polite" aria-atomic="true" className="sr-only" />;
}

/** Fetches the lazy concept files once the first view has painted, so (i) hints open instantly. */
function usePrefetchConcepts() {
  useEffect(() => {
    if ("requestIdleCallback" in window) {
      const handle = window.requestIdleCallback(() => void loadConcepts(), { timeout: 3000 });
      return () => window.cancelIdleCallback(handle);
    }
    const timer = globalThis.setTimeout(() => void loadConcepts(), 1500);
    return () => globalThis.clearTimeout(timer);
  }, []);
}

/**
 * The matched route and its validated search params, stamped on <main> as
 * data-route-id / data-route-search. A foundation-owned surface for e2e
 * specs (smoke checks deep links without reading any tab's copy).
 */
function useRouteStamp() {
  const routeId = useRouterState({ select: (state) => state.matches.at(-1)?.routeId ?? "" });
  const search = useRouterState({ select: (state) => JSON.stringify(state.matches.at(-1)?.search ?? {}) });
  return { routeId, search };
}

export function AppShell() {
  usePrefetchConcepts();
  const route = useRouteStamp();
  return (
    <TooltipProvider>
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-accent px-3 py-2 text-sm font-semibold text-accent-fg focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <div className="flex min-h-dvh flex-col">
        <TopNav />
        <main
          id="main"
          tabIndex={-1}
          data-route-id={route.routeId}
          data-route-search={route.search}
          className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 outline-none md:px-6 md:py-8"
        >
          <Outlet />
        </main>
        <footer className="border-t border-border">
          <div className="mx-auto flex max-w-[1440px] flex-col gap-1 px-4 py-4 text-xs text-text-3 md:flex-row md:items-center md:justify-between md:px-6">
            <p>
              {PRODUCT} reads this project&apos;s BigQuery tables through named, read-only queries (ADR 0042). Apache
              Beam™ is a trademark of the Apache Software Foundation.
            </p>
            <p className="flex gap-3">
              <Link to="/kit" className="text-link hover:underline">
                Design system
              </Link>
            </p>
          </div>
        </footer>
      </div>
      <Toaster />
      <RouteAnnouncer />
    </TooltipProvider>
  );
}
