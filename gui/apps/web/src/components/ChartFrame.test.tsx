import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ChartFrame } from "./ChartFrame";

// ECharts needs a real canvas; jsdom has none. The frame's own behaviour
// (title, hint, table twin, empty state) is what these tests pin.
vi.mock("./EChartCanvas", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div data-testid="echart-canvas" aria-label={ariaLabel} />,
}));

const rows = [
  { bucket: "0–10", source: 0.12, synthetic: 0.1 },
  { bucket: "10–20", source: 0.31, synthetic: 0.33 },
  { bucket: "20–30", source: null, synthetic: 0.57 },
];
const option = {
  xAxis: { type: "category" as const },
  yAxis: { type: "value" as const },
  series: [{ type: "bar" as const }],
};

// The chart canvas is a lazy import; on a loaded machine the first one can take seconds.
const LAZY = { timeout: 10_000 };

describe("ChartFrame", { timeout: 15_000 }, () => {
  it("renders a titled figure with the chart", async () => {
    render(<ChartFrame title="Histogram of order totals" option={option} data={rows} />);

    const figure = screen.getByRole("figure", { name: "Histogram of order totals" });
    expect(await within(figure).findByTestId("echart-canvas", {}, LAZY)).toBeInTheDocument();
  });

  it("toggles to a table with every data row and back", async () => {
    const user = userEvent.setup();
    render(<ChartFrame title="Histogram of order totals" option={option} data={rows} />);

    await user.click(screen.getByRole("button", { name: "View data" }));

    const table = screen.getByRole("table", { name: /Histogram of order totals/ });
    const bodyRows = within(table).getAllByRole("row").slice(1);
    expect(bodyRows).toHaveLength(rows.length);
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((th) => th.textContent),
    ).toEqual(["bucket", "source", "synthetic"]);
    expect(within(bodyRows[1]!).getByText("0.31")).toBeInTheDocument();
    expect(within(bodyRows[2]!).getByText("—")).toBeInTheDocument();
    expect(screen.queryByTestId("echart-canvas")).toBeNull();

    await user.click(screen.getByRole("button", { name: "View chart" }));
    expect(screen.queryByRole("table")).toBeNull();
    expect(await screen.findByTestId("echart-canvas", {}, LAZY)).toBeInTheDocument();
  });

  it("renders the empty-state message instead of the chart", () => {
    render(
      <ChartFrame
        title="Null rate"
        option={option}
        data={rows}
        empty={{ when: true, message: "Not evaluated: the reference sample is unverified." }}
      />,
    );

    expect(screen.getByText("Not evaluated: the reference sample is unverified.")).toBeInTheDocument();
    expect(screen.queryByTestId("echart-canvas")).toBeNull();
    expect(screen.queryByRole("button", { name: "View data" })).toBeNull();
  });

  it("treats an empty data set as empty rather than drawing blank axes", () => {
    render(<ChartFrame title="Fan-out" option={option} data={[]} />);

    expect(screen.getByText("No data to plot.")).toBeInTheDocument();
    expect(screen.queryByTestId("echart-canvas")).toBeNull();
  });

  it("puts a concept InfoHint next to the title", () => {
    render(<ChartFrame title="KS distance" concept="core:noise-floor" option={option} data={rows} />);

    expect(screen.getByRole("button", { name: "About: Noise floor" })).toBeInTheDocument();
  });
});
