import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { encodeVectorEnvelope } from "@contracts/vectors";

import { DataSourceBadge } from "@/components/DataSourceBadge";

import { api, ApiError, toQuery } from "./api";
import { useDataSource } from "./dataSource";

function respond(body: BodyInit | null, init: ResponseInit & { json?: boolean } = {}) {
  const headers = new Headers(init.headers);
  if (init.json !== false && typeof body === "string") headers.set("content-type", "application/json");
  return new Response(body, { status: init.status ?? 200, headers });
}

afterEach(() => vi.unstubAllGlobals());

describe("the API client", () => {
  it("encodes lists comma-separated and drops empty values", () => {
    expect(toQuery({ engine: ["b1_rag", "b2_library"], limit: 10, q: undefined, seed: "", tables: [] })).toBe(
      "engine=b1_rag%2Cb2_library&limit=10",
    );
  });

  it("returns the payload with the dry-run bytes and the data source", async () => {
    const fetch = vi.fn(() =>
      Promise.resolve(
        respond(JSON.stringify({ items: [], total: 0, offset: 0, limit: 50 }), {
          headers: { "x-bq-bytes-estimate": "1234", "x-data-source": "bigquery" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetch);
    const result = await api.evaluations({ engine: ["b1_rag"], limit: 50 });
    expect(fetch).toHaveBeenCalledWith("/api/evaluations?engine=b1_rag&limit=50", expect.anything());
    expect(result).toEqual({
      data: { items: [], total: 0, offset: 0, limit: 50 },
      bytesEstimate: 1234,
      dataSource: "bigquery",
      warnings: [],
    });
  });

  it("surfaces contract warnings and asks for profiles only on request", async () => {
    const fetch = vi.fn(() =>
      Promise.resolve(
        respond(JSON.stringify([]), {
          headers: {
            "x-data-source": "bigquery",
            "x-contract-warnings": JSON.stringify([
              'runs.list: status="NEW; OLD" not in the contract vocabulary',
              "dlq.summary: x",
            ]),
          },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetch);
    const runs = await api.runs({ limit: 5 });
    // A finding may contain "; " itself: the header is a JSON array, not a joined string.
    expect(runs.warnings).toEqual(['runs.list: status="NEW; OLD" not in the contract vocabulary', "dlq.summary: x"]);
    await api.evaluation("eval-0001");
    expect(fetch).toHaveBeenLastCalledWith("/api/evaluations/eval-0001", expect.anything());
    await api.evaluation("eval-0001", { profiles: "all" });
    expect(fetch).toHaveBeenLastCalledWith("/api/evaluations/eval-0001?profiles=all", expect.anything());
  });

  it("raises ApiError with the server's message and details", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve(
        respond(JSON.stringify({ message: "invalid request", details: ["limit: too small"] }), { status: 400 }),
      ),
    );
    await expect(api.evaluations({ limit: 0 })).rejects.toEqual(
      new ApiError(400, "invalid request", ["limit: too small"]),
    );
  });

  it("decodes the binary chunk envelope", async () => {
    const meta = [{ chunk_id: "a" }, { chunk_id: "b" }];
    const envelope = encodeVectorEnvelope(meta, new Float32Array([1, 0, 0, 1]), 2);
    vi.stubGlobal("fetch", () => Promise.resolve(respond(envelope as unknown as BodyInit, { json: false })));
    const { data } = await api.ragChunks({ digest: "d", kind: "row_doc", embedder: "hashing-384" });
    expect(data.meta).toEqual(meta);
    expect(Array.from(data.vectors)).toEqual([1, 0, 0, 1]);
  });
});

function Badge() {
  return <DataSourceBadge source={useDataSource()} />;
}

function renderBadge() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Badge />
    </QueryClientProvider>,
  );
}

describe("the data-source badge", () => {
  it("shows BIGQUERY <project> from /api/health", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve(
        respond(JSON.stringify({ status: "ok", mode: "bigquery", project: "demo-project", max_bytes_billed: 1 })),
      ),
    );
    renderBadge();
    expect(await screen.findByText("BIGQUERY demo-project")).toBeInTheDocument();
  });

  it("shows MOCK, and OFFLINE when the BFF does not answer", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve(respond(JSON.stringify({ status: "ok", mode: "mock", project: null }))),
    );
    const { unmount } = renderBadge();
    expect(await screen.findByText("MOCK")).toBeInTheDocument();
    unmount();
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("fetch failed")));
    renderBadge();
    expect(await screen.findByText("OFFLINE", {}, { timeout: 8000 })).toBeInTheDocument();
  });
});
