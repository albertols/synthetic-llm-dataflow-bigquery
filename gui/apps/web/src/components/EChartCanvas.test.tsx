import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EChartCanvas from "./EChartCanvas";

// A fake ECharts instance: the canvas wrapper's contract is what it calls.
const chart = {
  on: vi.fn(),
  off: vi.fn(),
  setOption: vi.fn(),
  setTheme: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
};
vi.mock("echarts/core", () => ({ use: vi.fn(), init: vi.fn(() => chart) }));

const rows = [{ x: "a", y: 1 }];
const option = {
  xAxis: { type: "category" as const },
  yAxis: { type: "value" as const },
  series: [{ type: "bar" as const }],
};

describe("EChartCanvas", () => {
  beforeEach(() => {
    for (const fn of Object.values(chart)) fn.mockClear();
  });

  it("renders once per real option change and hands the instance to onReady once", () => {
    const onReady = vi.fn();
    const { rerender } = render(
      <EChartCanvas option={option} data={rows} height={200} ariaLabel="c" onReady={onReady} />,
    );
    expect(chart.setOption).toHaveBeenCalledTimes(1);
    expect(chart.setOption.mock.calls[0]?.[0]).toMatchObject({ dataset: { source: rows } });
    expect(onReady).toHaveBeenCalledExactlyOnceWith(chart);

    // A new object with the same top-level parts is skipped.
    rerender(<EChartCanvas option={{ ...option }} data={rows} height={200} ariaLabel="c" onReady={onReady} />);
    expect(chart.setOption).toHaveBeenCalledTimes(1);

    rerender(
      <EChartCanvas
        option={{ ...option, series: [{ type: "line" as const }] }}
        data={rows}
        height={200}
        ariaLabel="c"
        onReady={onReady}
      />,
    );
    expect(chart.setOption).toHaveBeenCalledTimes(2);
    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it("binds onEvents with chart.on and rebinds when the map changes", () => {
    const click = vi.fn();
    const zoom = vi.fn();
    const { rerender, unmount } = render(
      <EChartCanvas option={option} data={rows} height={200} ariaLabel="c" onEvents={{ click }} />,
    );
    expect(chart.on).toHaveBeenCalledWith("click", click);

    rerender(<EChartCanvas option={option} data={rows} height={200} ariaLabel="c" onEvents={{ datazoom: zoom }} />);
    expect(chart.off).toHaveBeenCalledWith("click", click);
    expect(chart.on).toHaveBeenCalledWith("datazoom", zoom);

    unmount();
    expect(chart.off).toHaveBeenCalledWith("datazoom", zoom);
    expect(chart.dispose).toHaveBeenCalled();
  });
});
