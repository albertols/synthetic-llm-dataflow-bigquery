/**
 * Code-based routes (no generated route tree). Each tab owns its route
 * module under src/features/<tab>/route.tsx and exports, for every route it
 * serves, a zod `searchSchema` and a lazy `component`; this file only wires
 * them. Pages read typed params with `getRouteApi("<path>")`.
 *
 *   /                          INTRO
 *   /evaluation                EVALUATION list      (search: listSearchSchema)
 *   /evaluation/$evaluationId  EVALUATION run view  (search: runSearchSchema)
 *   /evaluation/compare        EVALUATION compare   (search: ids + filters)
 *   /rag                       RAG                  (search: table, digest, embedder)
 *   /config                    CONFIG               (search: knob, scenario)
 *   /kit                       design system reference (foundation)
 */
import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, createRoute, createRouter, lazyRouteComponent } from "@tanstack/react-router";

import { AppShell } from "./app/AppShell";
import { RouteErrorFallback } from "./app/ErrorBoundary";
import { NotFound } from "./app/NotFound";
import * as config from "./features/config/route";
import * as evaluation from "./features/evaluation/route";
import * as intro from "./features/intro/route";
import * as rag from "./features/rag/route";

export type RouterContext = { queryClient: QueryClient };

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: AppShell,
  notFoundComponent: NotFound,
});

const introRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  validateSearch: intro.searchSchema,
  component: intro.component,
  staticData: { title: "Intro" },
});

const evaluationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "evaluation",
  validateSearch: evaluation.listSearchSchema,
  component: evaluation.listComponent,
  staticData: { title: "Evaluation" },
});

const evaluationCompareRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "evaluation/compare",
  validateSearch: evaluation.compareSearchSchema,
  component: evaluation.compareComponent,
  staticData: { title: "Compare evaluations" },
});

const evaluationRunRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "evaluation/$evaluationId",
  validateSearch: evaluation.runSearchSchema,
  component: evaluation.runComponent,
  staticData: { title: "Evaluation run" },
});

const ragRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "rag",
  validateSearch: rag.searchSchema,
  component: rag.component,
  staticData: { title: "RAG" },
});

const configRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "config",
  validateSearch: config.searchSchema,
  component: config.component,
  staticData: { title: "Config" },
});

const kitRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "kit",
  component: lazyRouteComponent(() => import("./app/kit/KitPage"), "KitPage"),
  staticData: { title: "Design system" },
});

export const routeTree = rootRoute.addChildren([
  introRoute,
  evaluationRoute,
  evaluationCompareRoute,
  evaluationRunRoute,
  ragRoute,
  configRoute,
  kitRoute,
]);

export function createAppRouter(queryClient: QueryClient) {
  return createRouter({
    routeTree,
    context: { queryClient },
    defaultPreload: "intent",
    defaultPreloadStaleTime: 0,
    defaultErrorComponent: RouteErrorFallback,
    defaultNotFoundComponent: NotFound,
    scrollRestoration: true,
  });
}

export type AppRouter = ReturnType<typeof createAppRouter>;

declare module "@tanstack/react-router" {
  interface Register {
    router: AppRouter;
  }
  interface StaticDataRouteOption {
    /** Page title: document.title and the route announcement. */
    title?: string;
  }
}
