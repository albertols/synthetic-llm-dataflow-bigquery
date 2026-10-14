import { Check, ChevronDown, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { cn } from "@/lib/cn";

export type ComboboxOption = { value: string; label: string; description?: string; disabled?: boolean };

type CommonProps = {
  options: readonly ComboboxOption[];
  /** Accessible name of the input and its listbox (e.g. "Engine"). */
  label: string;
  placeholder?: string;
  emptyText?: string;
  disabled?: boolean;
  className?: string;
  id?: string;
};
export type ComboboxSingleProps = CommonProps & {
  multiple?: false;
  value: string | null;
  onValueChange: (value: string | null) => void;
};
export type ComboboxMultiProps = CommonProps & {
  multiple: true;
  value: readonly string[];
  onValueChange: (value: string[]) => void;
};
export type ComboboxProps = ComboboxSingleProps | ComboboxMultiProps;

/**
 * Filterable select (WAI-ARIA 1.2 combobox with a listbox popup). Single or
 * multiple (`multiple` keeps the list open and toggles). Keyboard: type to
 * filter, ↓/↑ move, Enter picks, Esc closes (then clears the query), Tab leaves.
 */
export function Combobox(props: ComboboxProps) {
  const { options, label, placeholder = "Select…", emptyText = "No matches", disabled, className } = props;
  const autoId = useId();
  const inputId = props.id ?? `cb-${autoId}`;
  const listId = `${inputId}-list`;
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  const selected = useMemo(
    () => new Set(props.multiple ? props.value : props.value === null ? [] : [props.value]),
    [props.multiple, props.value],
  );
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) =>
      [o.label, o.value, o.description ?? ""].some((text) => text.toLowerCase().includes(q)),
    );
  }, [options, query]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const optionId = (index: number) => `${inputId}-opt-${index}`;
  const clampedActive = Math.min(active, Math.max(filtered.length - 1, 0));

  function choose(option: ComboboxOption) {
    if (option.disabled) return;
    if (props.multiple) {
      const next = selected.has(option.value)
        ? props.value.filter((v) => v !== option.value)
        : [...props.value, option.value];
      props.onValueChange(next);
    } else {
      props.onValueChange(option.value);
      setOpen(false);
      setQuery("");
    }
  }

  function clear() {
    if (props.multiple) props.onValueChange([]);
    else props.onValueChange(null);
    setQuery("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open) setOpen(true);
        else setActive((i) => Math.min(i + 1, filtered.length - 1));
        break;
      case "ArrowUp":
        event.preventDefault();
        setActive((i) => Math.max(i - 1, 0));
        break;
      case "Home":
        if (open) {
          event.preventDefault();
          setActive(0);
        }
        break;
      case "End":
        if (open) {
          event.preventDefault();
          setActive(Math.max(filtered.length - 1, 0));
        }
        break;
      case "Enter": {
        const option = filtered[clampedActive];
        if (open && option) {
          event.preventDefault();
          choose(option);
        }
        break;
      }
      case "Escape":
        if (open) {
          event.preventDefault();
          setOpen(false);
        } else if (query) {
          setQuery("");
        }
        break;
      case "Tab":
        setOpen(false);
        setQuery("");
        break;
    }
  }

  const singleLabel =
    !props.multiple && props.value !== null ? options.find((o) => o.value === props.value)?.label : undefined;
  const summary = props.multiple
    ? selected.size === 0
      ? placeholder
      : selected.size === 1
        ? (options.find((o) => selected.has(o.value))?.label ?? "1 selected")
        : `${selected.size} selected`
    : placeholder;
  const inputValue = open ? query : (singleLabel ?? "");
  const hasValue = selected.size > 0;

  return (
    <div ref={rootRef} className={cn("relative w-full min-w-40", className)}>
      <div
        className={cn(
          "flex h-9 items-center rounded-md border border-control-border bg-surface-1 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-focus-ring",
          disabled && "opacity-50",
        )}
      >
        <input
          id={inputId}
          type="text"
          role="combobox"
          aria-label={label}
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={open && filtered.length ? optionId(clampedActive) : undefined}
          autoComplete="off"
          disabled={disabled}
          placeholder={summary}
          value={inputValue}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
            setOpen(true);
          }}
          onClick={() => setOpen(true)}
          onKeyDown={onKeyDown}
          className="h-full min-w-0 flex-1 bg-transparent pl-3 text-sm text-text-1 outline-none placeholder:text-text-3"
        />
        {hasValue ? (
          <button
            type="button"
            aria-label={`Clear ${label}`}
            onClick={clear}
            disabled={disabled}
            className="inline-flex size-7 cursor-pointer items-center justify-center rounded-sm text-text-3 hover:text-text-1"
          >
            <X className="size-3.5" aria-hidden="true" />
          </button>
        ) : null}
        <ChevronDown className="mr-2 size-4 shrink-0 text-text-3" aria-hidden="true" />
      </div>
      <ul
        id={listId}
        role="listbox"
        aria-label={label}
        aria-multiselectable={props.multiple || undefined}
        hidden={!open || filtered.length === 0}
        className="absolute top-full right-0 left-0 z-50 mt-1 max-h-64 overflow-auto rounded-md border border-border-strong bg-surface-2 p-1 shadow-popover"
      >
        {filtered.map((option, index) => {
          const isSelected = selected.has(option.value);
          return (
            // Keyboard selection lives on the input (aria-activedescendant pattern); options take pointer input only.
            // eslint-disable-next-line jsx-a11y/click-events-have-key-events
            <li
              key={option.value}
              id={optionId(index)}
              role="option"
              aria-selected={isSelected}
              aria-disabled={option.disabled || undefined}
              onPointerDown={(event) => event.preventDefault()}
              onPointerMove={() => setActive(index)}
              onClick={() => choose(option)}
              className={cn(
                "flex cursor-pointer items-start gap-2 rounded-sm px-2 py-1.5 text-sm text-text-1",
                index === clampedActive && "bg-surface-3",
                option.disabled && "cursor-not-allowed opacity-50",
              )}
            >
              <Check
                className={cn("mt-0.5 size-4 shrink-0 text-accent-text", !isSelected && "invisible")}
                aria-hidden="true"
              />
              <span className="grid">
                <span>{option.label}</span>
                {option.description ? <span className="text-xs text-text-3">{option.description}</span> : null}
              </span>
            </li>
          );
        })}
      </ul>
      {open && filtered.length === 0 ? (
        <div
          role="status"
          className="absolute top-full right-0 left-0 z-50 mt-1 rounded-md border border-border-strong bg-surface-2 px-3 py-2 text-sm text-text-3 shadow-popover"
        >
          {emptyText}
        </div>
      ) : null}
    </div>
  );
}
