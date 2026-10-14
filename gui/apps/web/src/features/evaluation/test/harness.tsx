/**
 * Renders the real route tree at a URL with a fresh QueryClient and a stubbed
 * BFF: `api` maps a path ("/api/evaluations/eval-t001") to a JSON body or a
 * function of the URL. Unknown paths answer 404, so a test sees every call it
 * did not expect. ECharts is replaced by a labelled div (jsdom has no canvas).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRouter, RouterProvider } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import { vi } from "vitest";

import { routeTree } from "@/router";

/** A JSON body, or a function of the request URL that returns one. */
export type ApiStub = Record<string, unknown>;

export function stubApi(api: ApiStub) {
  const calls: URL[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = new URL(
      typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
      "http://localhost",
    );
    calls.push(url);
    const handler = api[url.pathname];
    if (handler === undefined) {
      return Promise.resolve(
        new Response(JSON.stringify({ statusCode: 404, error: "Not Found", message: `no stub for ${url.pathname}` }), {
          status: 404,
          headers: { "content-type": "application/json" },
        }),
      );
    }
    const body = typeof handler === "function" ? (handler as (u: URL) => unknown)(url) : handler;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json", "x-data-source": "mock" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

export const EMPTY_PAGE = { items: [], total: 0, offset: 0, limit: 1 };

/** A `/api/evaluations` page holding these rows (the run view looks its id up there first). */
export function pageOf(...items: object[]) {
  return { items, total: items.length, offset: 0, limit: 50 };
}

export function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
    context: { queryClient },
  });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...utils, router, queryClient };
}
