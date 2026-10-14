/**
 * The brand and theme switcher, with the build's head written for the
 * committed brand and the fictitious "acme" fixture (marked default): two
 * labelled radio groups in one menu, keyboard-operable, whose choices land on
 * <html> and in storage; and the active pack's logo beside the product name.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { BRAND_STORAGE_KEY } from "@/lib/brand";
import { THEME_STORAGE_KEY } from "@/lib/theme";

import { brandRegistry, discoverBrandPacks, injectBrandHead, packRoots } from "../../vite-plugins/brandPacks";
import { AppearanceMenu } from "./Appearance";
import { BrandLockup } from "./BrandLockup";
import { ThemeProvider } from "./ThemeProvider";

function webRoot(): string {
  let dir = process.cwd();
  while (!existsSync(join(dir, "apps", "web", "index.html"))) {
    const parent = dirname(dir);
    if (parent === dir) throw new Error("gui/ not found above the working directory");
    dir = parent;
  }
  return join(dir, "apps", "web");
}

const WEB = webRoot();
const HTML = injectBrandHead(
  readFileSync(join(WEB, "index.html"), "utf8"),
  brandRegistry(discoverBrandPacks([packRoots(WEB)[0]!, join(WEB, "src", "test", "fixtures", "brands")])),
);
const HEAD = HTML.slice(HTML.indexOf("<head>") + 6, HTML.indexOf("</head>"));

function renderSwitcher() {
  return render(
    <ThemeProvider>
      <TooltipProvider>
        <BrandLockup />
        <AppearanceMenu />
      </TooltipProvider>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  document.head.innerHTML = HEAD;
});

afterEach(() => {
  window.localStorage.clear();
  document.head.innerHTML = "";
  document.documentElement.removeAttribute("data-brand");
  document.documentElement.removeAttribute("data-theme");
});

describe("the brand and theme switcher", () => {
  it("lists the brands and the modes as two labelled radio groups, the defaults checked", async () => {
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: "Brand and theme" }));
    const brands = screen.getByRole("group", { name: "Brand" });
    expect(
      within(brands)
        .getAllByRole("menuitemradio")
        .map((item) => item.textContent),
    ).toEqual(["Beam · Dataflow", "Acme Analytics"]);
    expect(within(brands).getByRole("menuitemradio", { name: "Acme Analytics" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    const modes = screen.getByRole("group", { name: "Theme" });
    expect(
      within(modes)
        .getAllByRole("menuitemradio")
        .map((item) => item.textContent),
    ).toEqual(["Dark", "Light", "Match system"]);
    expect(within(modes).getByRole("menuitemradio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
  });

  it("puts each choice on <html> and in storage, independently", async () => {
    renderSwitcher();
    const open = () => userEvent.click(screen.getByRole("button", { name: "Brand and theme" }));

    await open();
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Light" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(document.documentElement).toHaveAttribute("data-brand", "acme");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute("content", "#0f3b3a");

    await open();
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Beam · Dataflow" }));
    expect(document.documentElement).toHaveAttribute("data-brand", "beam-dataflow");
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(window.localStorage.getItem(BRAND_STORAGE_KEY)).toBe("beam-dataflow");
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute("content", "#f4f5f7");
  });

  it("works from the keyboard", async () => {
    renderSwitcher();
    const user = userEvent.setup();
    await user.tab();
    expect(screen.getByRole("button", { name: "Brand and theme" })).toHaveFocus();
    await user.keyboard("{Enter}");
    const items = screen.getAllByRole("menuitemradio");
    // Beam · Dataflow, Acme Analytics, Dark, Light, Match system: down to "Light".
    for (const _ of items.slice(0, 3)) await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitemradio", { name: "Light" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("starts from a stored brand, and from the default when the stored one is gone", () => {
    window.localStorage.setItem(BRAND_STORAGE_KEY, "removed-pack");
    const { unmount } = renderSwitcher();
    expect(document.querySelector("[data-brand-logo]")).toHaveAttribute("data-brand-logo", "acme");
    unmount();
    window.localStorage.setItem(BRAND_STORAGE_KEY, "beam-dataflow");
    renderSwitcher();
    expect(document.querySelector("[data-brand-logo]")).toBeNull();
  });
});

describe("the brand's logo in the banner", () => {
  it("shows the active pack's logo for the mode, with its name in text", async () => {
    renderSwitcher();
    const lockup = document.querySelector("[data-brand-logo='acme']")!;
    expect(lockup.querySelector("img")?.getAttribute("src")).toMatch(/^data:image\/svg\+xml;base64,/);
    expect(lockup).toHaveTextContent("Acme Analytics");
    await userEvent.click(screen.getByRole("button", { name: "Brand and theme" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Beam · Dataflow" }));
    expect(document.querySelector("[data-brand-logo]")).toBeNull();
  });
});
