import { Slider as SliderPrimitive } from "radix-ui";
import { useState, type ComponentPropsWithRef } from "react";

import { cn } from "@/lib/cn";

export type SliderProps = ComponentPropsWithRef<typeof SliderPrimitive.Root> & {
  /** One accessible name per thumb, e.g. ["Minimum similarity", "Maximum similarity"]. */
  thumbLabels: string[];
  /** Formats the thumb's aria-valuetext ("0.30", "10,000 rows"). */
  formatValue?: (value: number) => string;
};

/** Single or range slider. Keyboard: arrows step, PageUp/PageDown step ×10, Home/End jump. */
export function Slider({ className, thumbLabels, formatValue, value, defaultValue, ...props }: SliderProps) {
  // Uncontrolled sliders keep their own copy so each thumb's aria-valuetext follows the drag.
  const [uncontrolled, setUncontrolled] = useState(() => defaultValue ?? [props.min ?? 0]);
  const values = value ?? uncontrolled;
  return (
    <SliderPrimitive.Root
      value={value}
      defaultValue={defaultValue}
      className={cn(
        "relative flex w-full touch-none items-center select-none data-[disabled]:opacity-50 data-[orientation=vertical]:h-40 data-[orientation=vertical]:w-auto data-[orientation=vertical]:flex-col",
        className,
      )}
      {...props}
      onValueChange={(next) => {
        if (value === undefined) setUncontrolled(next);
        props.onValueChange?.(next);
      }}
    >
      <SliderPrimitive.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-surface-3 data-[orientation=vertical]:h-full data-[orientation=vertical]:w-1.5">
        <SliderPrimitive.Range className="absolute h-full bg-accent data-[orientation=vertical]:w-full" />
      </SliderPrimitive.Track>
      {values.map((thumbValue, index) => (
        <SliderPrimitive.Thumb
          key={index}
          aria-label={thumbLabels[index] ?? thumbLabels[0]}
          aria-valuetext={formatValue ? formatValue(thumbValue) : undefined}
          className="block size-4 cursor-grab rounded-full border-2 border-accent bg-surface-1 shadow transition-transform hover:scale-110 active:cursor-grabbing"
        />
      ))}
    </SliderPrimitive.Root>
  );
}
