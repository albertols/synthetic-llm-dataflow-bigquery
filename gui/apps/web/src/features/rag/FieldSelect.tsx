import { useId, type ReactNode } from "react";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/cn";

export interface FieldOption {
  value: string;
  label: string;
  /** Second line in the list (dates, counts). */
  hint?: string;
  disabled?: boolean;
}

/** A labelled Radix Select: the label is visible and names the trigger. */
export function FieldSelect({
  label,
  value,
  options,
  onChange,
  hint,
  className,
  triggerClassName,
}: {
  label: string;
  value: string;
  options: readonly FieldOption[];
  onChange: (value: string) => void;
  /** An (i) or badge after the label. */
  hint?: ReactNode;
  className?: string;
  triggerClassName?: string;
}) {
  const id = useId();
  return (
    <div className={cn("grid min-w-0 gap-1", className)}>
      <div className="flex items-center gap-1">
        <label htmlFor={id} className="text-xs font-medium text-text-2">
          {label}
        </label>
        {hint}
      </div>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id} className={cn("min-w-0", triggerClassName)}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value} disabled={option.disabled}>
              {option.label}
              {option.hint ? <span className="text-text-3"> · {option.hint}</span> : null}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
