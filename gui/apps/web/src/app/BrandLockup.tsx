import { useTheme } from "@/lib/theme";

/**
 * The active brand pack's logo, beside the product name and apart from it (a
 * divider, its own element: never part of the product's link or name). The
 * committed brand carries no logo, so it shows nothing. A logo that spells the
 * name stands alone; otherwise the pack's name follows it in text from 1024 px
 * (screen readers always get the name).
 */
export function BrandLockup() {
  const { brand, brands, resolved } = useTheme();
  const entry = brands.find((b) => b.id === brand);
  const logo = entry?.logo;
  if (!entry || !logo) return null;
  const image = (
    <img
      src={logo[resolved]}
      alt={logo.wordmark ? entry.name : ""}
      className={logo.plate ? "h-[22px] w-auto max-w-36 object-contain" : "h-6 w-auto max-w-40 object-contain"}
    />
  );
  return (
    <span className="flex h-14 shrink-0 items-center gap-2.5" data-brand-logo={entry.id}>
      <span aria-hidden="true" className="h-6 w-px bg-border-strong" />
      {logo.plate ? (
        <span className="inline-flex rounded-[3px] p-[3px]" style={{ background: logo.plate }}>
          {image}
        </span>
      ) : (
        image
      )}
      {logo.wordmark ? null : (
        <span className="sr-only text-[15px] font-semibold tracking-tight whitespace-nowrap text-text-1 lg:not-sr-only">
          {entry.name}
        </span>
      )}
    </span>
  );
}
