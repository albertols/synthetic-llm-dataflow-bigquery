import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { getConcept } from "@/lib/concepts";

import { InfoHint } from "./InfoHint";

const ID = "core:noise-floor";
const sleep = (ms: number) => act(() => new Promise((resolve) => setTimeout(resolve, ms)));

// KaTeX and the tab concept files are lazy imports; on a loaded machine they can take seconds.
const LAZY = { timeout: 10_000 };

describe("InfoHint", { timeout: 15_000 }, () => {
  it("uses a registered concept as its test fixture", () => {
    const concept = getConcept(ID);
    expect(concept?.title).toBe("Noise floor");
    expect(concept?.formula).toBeTruthy();
    expect(concept?.links.length).toBeGreaterThan(0);
  });

  it("opens on hover after the delay and renders the title and the formula", async () => {
    const user = userEvent.setup();
    render(<InfoHint concept={ID} />);
    const trigger = screen.getByRole("button", { name: "About: Noise floor" });

    await user.hover(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Noise floor" });

    expect(within(dialog).getByRole("heading", { name: "Noise floor" })).toBeInTheDocument();
    // KaTeX loads lazily; the rendered formula carries MathML for screen readers.
    await waitFor(() => expect(dialog.querySelector(".katex")).not.toBeNull(), LAZY);
    expect(dialog.querySelector("math")).not.toBeNull();
  });

  it("does not open when the pointer only passes over it", async () => {
    const user = userEvent.setup();
    render(<InfoHint concept={ID} />);
    const trigger = screen.getByRole("button", { name: "About: Noise floor" });

    await user.hover(trigger);
    await user.unhover(trigger);
    await sleep(300);

    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens on Enter, closes on Escape and returns focus to the trigger", async () => {
    const user = userEvent.setup();
    render(<InfoHint concept={ID} />);
    const trigger = screen.getByRole("button", { name: "About: Noise floor" });

    await user.tab();
    expect(trigger).toHaveFocus();
    await user.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog", { name: "Noise floor" });
    expect(within(dialog).getByText(/sampling noise alone/)).toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(trigger).toHaveFocus();
  });

  it("opens on click and stays open while the pointer moves into it", async () => {
    const user = userEvent.setup();
    render(<InfoHint concept={ID} />);
    const trigger = screen.getByRole("button", { name: "About: Noise floor" });

    await user.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Noise floor" });
    await user.unhover(trigger);
    await user.hover(dialog);
    await sleep(300);

    expect(screen.getByRole("dialog", { name: "Noise floor" })).toBeInTheDocument();
  });

  it("renders interpretation and links that open in a new tab with rel=noopener noreferrer", async () => {
    const user = userEvent.setup();
    render(<InfoHint concept={ID} />);
    await user.click(screen.getByRole("button", { name: "About: Noise floor" }));
    const dialog = await screen.findByRole("dialog", { name: "Noise floor" });

    expect(within(dialog).getByText(/Below the floor/)).toBeInTheDocument();
    const links = within(dialog).getAllByRole("link");
    expect(links).toHaveLength(getConcept(ID)?.links.length ?? -1);
    for (const link of links) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
      expect(link.getAttribute("href")).toMatch(/^https:\/\//);
    }
    expect(within(dialog).getByRole("link", { name: /Massart 1990/ })).toHaveAccessibleName(/opens in a new tab/);
  });

  it("logs an unknown concept id and renders a neutral, non-interactive icon", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<InfoHint concept="core:not-a-concept" />);

    // Unknown only once every lazy concept file has been merged.
    expect(await screen.findByRole("img", { name: "No explanation available" }, LAZY)).toBeInTheDocument();
    expect(error).toHaveBeenCalledWith(expect.stringContaining('"core:not-a-concept"'));
    expect(screen.queryByRole("button")).toBeNull();
  });
});
