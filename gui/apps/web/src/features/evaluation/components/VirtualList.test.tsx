import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VirtualList, visibleRange } from "./VirtualList";

const ITEMS = Array.from({ length: 120 }, (_, i) => `T_${String(i + 1).padStart(3, "0")}`);

function renderList(items: readonly string[], scrollToIndex?: number) {
  return render(
    <VirtualList
      items={items}
      rowHeight={24}
      label="Tables"
      getKey={(t) => t}
      renderItem={(t) => <span>{t}</span>}
      scrollToIndex={scrollToIndex}
    />,
  );
}

describe("visibleRange", () => {
  it.each([
    [0, 288, 24, 120, 4, { start: 0, end: 17 }],
    [480, 288, 24, 120, 4, { start: 16, end: 37 }],
    [2592, 288, 24, 120, 4, { start: 104, end: 120 }],
    [0, 288, 24, 0, 4, { start: 0, end: 0 }],
  ])("scrollTop %i, viewport %i, rows of %i, %i items, overscan %i", (top, viewport, row, count, overscan, range) => {
    expect(visibleRange(top, viewport, row, count, overscan)).toEqual(range);
  });
});

describe("VirtualList", () => {
  it("renders every item, with no scroll frame, up to twelve rows", () => {
    renderList(ITEMS.slice(0, 12));
    expect(screen.getByRole("list", { name: "Tables" }).querySelectorAll("li")).toHaveLength(12);
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("renders only the visible window of a long list, and the rest on scroll", () => {
    renderList(ITEMS);
    const frame = screen.getByRole("region", { name: "Tables (120, scrolls)" });
    expect(frame).toHaveAttribute("tabindex", "0");
    expect(frame).toHaveStyle({ height: "288px" });
    const list = within(frame).getByRole("list", { name: "Tables" });
    // The scrollbar spans every row, rendered or not: 120 rows of 24 px.
    expect(list).toHaveStyle({ height: "2880px" });
    expect(list.querySelectorAll("li")).toHaveLength(17);
    expect(list.querySelector("li")).toHaveAttribute("aria-setsize", "120");
    fireEvent.scroll(frame, { target: { scrollTop: 24 * 108 } });
    expect(within(list).getByText("T_120")).toBeInTheDocument();
    expect(within(list).queryByText("T_001")).toBeNull();
    expect(list.querySelector('li[aria-posinset="120"]')).not.toBeNull();
  });

  it("scrolls a chosen row into view", () => {
    renderList(ITEMS, 99);
    expect(within(screen.getByRole("list", { name: "Tables" })).getByText("T_100")).toBeInTheDocument();
    // The frame itself moved, not only the rendered window: one row above the chosen one.
    expect(screen.getByRole("region")).toHaveProperty("scrollTop", 98 * 24);
  });

  it("keeps a focused row rendered when it scrolls out of the window, so focus never drops to <body>", () => {
    render(
      <VirtualList
        items={ITEMS}
        rowHeight={24}
        label="Tables"
        getKey={(t) => t}
        renderItem={(t) => <button type="button">{t}</button>}
      />,
    );
    const button = screen.getByRole("button", { name: "T_002" });
    act(() => button.focus());
    const frame = screen.getByRole("region");
    fireEvent.scroll(frame, { target: { scrollTop: 24 * 100 } });
    const list = within(frame).getByRole("list", { name: "Tables" });
    expect(within(list).getByText("T_110")).toBeInTheDocument();
    expect(within(list).queryByText("T_003")).toBeNull();
    expect(button).toHaveFocus();
    expect(button.closest("li")).toHaveAttribute("aria-posinset", "2");
    // Once focus leaves the list, the row leaves the DOM with the rest of the window.
    act(() => button.blur());
    expect(within(list).queryByText("T_002")).toBeNull();
  });

  it("never renders past the end after a filter shortens the list", () => {
    const { rerender } = renderList(ITEMS);
    fireEvent.scroll(screen.getByRole("region"), { target: { scrollTop: 24 * 108 } });
    rerender(
      <VirtualList
        items={ITEMS.slice(0, 20)}
        rowHeight={24}
        label="Tables"
        getKey={(t) => t}
        renderItem={(t) => <span>{t}</span>}
      />,
    );
    expect(within(screen.getByRole("list", { name: "Tables" })).getByText("T_020")).toBeInTheDocument();
  });
});
