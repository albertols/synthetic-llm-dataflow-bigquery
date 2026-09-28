import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Slider } from "./slider";

const format = (v: number) => `${v} rows`;

describe("Slider", () => {
  it("keeps aria-valuetext current on an uncontrolled slider", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    render(
      <Slider
        defaultValue={[10]}
        min={0}
        max={100}
        step={10}
        thumbLabels={["Rows"]}
        formatValue={format}
        onValueChange={onValueChange}
      />,
    );
    const thumb = screen.getByRole("slider", { name: "Rows" });
    expect(thumb).toHaveAttribute("aria-valuetext", "10 rows");
    thumb.focus();
    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(thumb).toHaveAttribute("aria-valuenow", "30");
    expect(thumb).toHaveAttribute("aria-valuetext", "30 rows");
    expect(onValueChange).toHaveBeenLastCalledWith([30]);
  });

  it("follows the value prop when controlled", async () => {
    const user = userEvent.setup();
    function Controlled() {
      const [value, setValue] = useState([0.3, 0.7]);
      return (
        <Slider
          value={value}
          onValueChange={setValue}
          min={0}
          max={1}
          step={0.1}
          thumbLabels={["Minimum similarity", "Maximum similarity"]}
          formatValue={(v) => v.toFixed(1)}
        />
      );
    }
    render(<Controlled />);
    const low = screen.getByRole("slider", { name: "Minimum similarity" });
    low.focus();
    await user.keyboard("{ArrowRight}");
    expect(low).toHaveAttribute("aria-valuetext", "0.4");
    expect(screen.getByRole("slider", { name: "Maximum similarity" })).toHaveAttribute("aria-valuetext", "0.7");
  });
});
