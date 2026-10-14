import { Monitor, Moon, Palette, Sun } from "lucide-react";
import { useId } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/ui/tooltip";
import { useTheme, type ThemePreference } from "@/lib/theme";

const MODES: ReadonlyArray<{ value: ThemePreference; label: string; icon: typeof Moon }> = [
  { value: "dark", label: "Dark", icon: Moon },
  { value: "light", label: "Light", icon: Sun },
  { value: "system", label: "Match system", icon: Monitor },
];

const isPreference = (value: string): value is ThemePreference => MODES.some((mode) => mode.value === value);

/**
 * The two independent choices, as two labelled radio groups of one menu: the
 * brand (the build's packs) and the mode. Arrow keys move, Enter or Space
 * picks, Esc closes; each item is a `menuitemradio` with `aria-checked`.
 */
export function AppearanceItems() {
  const { brand, brands, setBrand, preference, setPreference } = useTheme();
  const brandLabel = useId();
  const modeLabel = useId();
  return (
    <>
      <DropdownMenuLabel id={brandLabel}>Brand</DropdownMenuLabel>
      <DropdownMenuRadioGroup aria-labelledby={brandLabel} value={brand} onValueChange={setBrand}>
        {brands.map((entry) => (
          <DropdownMenuRadioItem key={entry.id} value={entry.id}>
            {entry.name}
          </DropdownMenuRadioItem>
        ))}
      </DropdownMenuRadioGroup>
      <DropdownMenuSeparator />
      <DropdownMenuLabel id={modeLabel}>Theme</DropdownMenuLabel>
      <DropdownMenuRadioGroup
        aria-labelledby={modeLabel}
        value={preference}
        onValueChange={(value) => {
          if (isPreference(value)) setPreference(value);
        }}
      >
        {MODES.map(({ value, label, icon: Icon }) => (
          <DropdownMenuRadioItem key={value} value={value}>
            <Icon aria-hidden="true" />
            {label}
          </DropdownMenuRadioItem>
        ))}
      </DropdownMenuRadioGroup>
    </>
  );
}

/** The brand and theme switcher in the top bar (640 px and up; below, the "More" menu carries the same items). */
export function AppearanceMenu() {
  return (
    // Non-modal: a modal menu aria-hides the page behind it, which axe reports as aria-hidden-focus.
    <DropdownMenu modal={false}>
      <Tooltip content="Brand and theme">
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="Brand and theme">
            <Palette aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
      </Tooltip>
      <DropdownMenuContent>
        <AppearanceItems />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
