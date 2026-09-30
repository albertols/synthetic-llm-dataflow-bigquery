/**
 * A metric's (i) inside an EVALUATION page carries "Related knob" links: real
 * router links (keyboard reachable) to the CONFIG knob sheet.
 */
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { InfoHint } from "@/components/InfoHint";
import * as config from "@/features/config/route";

import { RelatedKnobsProvider } from "./components/RelatedKnobs";

const LAZY = { timeout: 10_000 };

function renderAt(page: ReactNode) {
  const root = createRootRoute({ component: () => <Outlet /> });
  const home = createRoute({ getParentRoute: () => root, path: "/", component: () => page });
  const configRoute = createRoute({
    getParentRoute: () => root,
    path: "/config",
    validateSearch: config.searchSchema,
    component: () => <p>Config tab</p>,
  });
  const router = createRouter({
    routeTree: root.addChildren([home, configRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  render(<RouterProvider router={router} />);
  return router;
}

describe("related knobs in a metric's (i)", { timeout: 20_000 }, () => {
  it("link the pool-cap metric to its knob sheet, reachable by keyboard", async () => {
    const user = userEvent.setup();
    const router = renderAt(
      <RelatedKnobsProvider>
        <InfoHint concept="metric:column.distinct_ceiling_hit" />
      </RelatedKnobsProvider>,
    );
    const trigger = await screen.findByRole("button", { name: /About: Distinct-count ceiling hit/ }, LAZY);
    trigger.focus();
    await user.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog", { name: /Distinct-count ceiling hit/ });
    const link = within(dialog).getByRole("link", { name: /free_text_pool_max\s*— open its sheet in Config/ });
    expect(link).toHaveAttribute("href", "/config?section=amp&knob=free_text_pool_max");
    expect(within(dialog).getByTestId("related-knobs")).toHaveTextContent(/lands on this cap/);

    // Tab walks from the popover's own links to the related knob, and Enter follows it.
    for (let i = 0; i < 10 && document.activeElement !== link; i += 1) await user.tab();
    expect(link).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Config tab")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/config");
    expect(router.state.location.search).toEqual({ section: "amp", knob: "free_text_pool_max" });
  });

  it("add the reference sample size to a metric with a stored baseline, and nothing to other concepts", async () => {
    const user = userEvent.setup();
    renderAt(
      <RelatedKnobsProvider>
        <InfoHint concept="metric:column.dow_tvd" />
        <InfoHint concept="core:noise-floor" />
      </RelatedKnobsProvider>,
    );
    await user.click(await screen.findByRole("button", { name: /About: Day-of-week mix/ }, LAZY));
    const dialog = await screen.findByRole("dialog", { name: /Day-of-week mix/ });
    expect(within(dialog).getByText("Related knobs")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: /similarity/ })).toHaveAttribute(
      "href",
      "/config?section=amp&knob=similarity",
    );
    expect(within(dialog).getByRole("link", { name: /reference_rows_limit/ })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "About: Noise floor" }));
    const core = await screen.findByRole("dialog", { name: "Noise floor" });
    expect(within(core).queryByTestId("related-knobs")).toBeNull();
  });

  it("show nothing outside an EVALUATION page (no provider)", async () => {
    const user = userEvent.setup();
    renderAt(<InfoHint concept="metric:column.distinct_ceiling_hit" />);
    await user.click(await screen.findByRole("button", { name: /About: Distinct-count ceiling hit/ }, LAZY));
    const dialog = await screen.findByRole("dialog", { name: /Distinct-count ceiling hit/ });
    expect(within(dialog).queryByTestId("related-knobs")).toBeNull();
  });
});
