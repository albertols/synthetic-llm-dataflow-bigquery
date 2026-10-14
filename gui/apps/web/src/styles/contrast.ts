/**
 * WCAG 2.x contrast, for the tests that hold the palettes to it
 * (branding.contrast.test.ts for BRANDING.md's tables, brands/brandContrast.test.ts
 * for every brand pack). The dataviz skill's `contrast()`.
 */

export type Rgb = [number, number, number];
export type Tint = { rgb: Rgb; alpha: number };
export type Theme = "dark" | "light";

export const rgbOf = (hex: string): Rgb => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as Rgb;

/** WCAG 2.x relative luminance. */
export function luminance(color: string | Rgb): number {
  const [r, g, b] = (typeof color === "string" ? rgbOf(color) : color).map((channel) => {
    const v = channel / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

export function contrast(a: string | Rgb, b: string | Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

/** A tint composited over a surface (source-over, per channel), rounded to the 8-bit pixel the screen shows. */
export const over = (tint: Tint, surface: string | Rgb): Rgb =>
  tint.rgb.map((c, i) =>
    Math.round(c * tint.alpha + (typeof surface === "string" ? rgbOf(surface) : surface)[i]! * (1 - tint.alpha)),
  ) as Rgb;

/** `rgb(r g b / a)` → a tint; anything else → null. */
export function parseTint(value: string): Tint | null {
  const m = /^rgb\((\d+) (\d+) (\d+) \/ ([\d.]+)\)$/.exec(value.trim());
  return m ? { rgb: [Number(m[1]), Number(m[2]), Number(m[3])], alpha: Number(m[4]) } : null;
}

/** The text of tokens.css's block for one theme: `:root[data-theme="dark"] {` … `}`. */
export function themeBlock(css: string, theme: Theme): string {
  const start = css.indexOf(`:root[data-theme="${theme}"] {`);
  if (start < 0) throw new Error(`tokens.css has no :root[data-theme="${theme}"] block`);
  return css.slice(start, css.indexOf("\n}", start));
}

/** `--name: #hex;` pairs of one block. */
export function blockColours(block: string): Map<string, string> {
  return new Map([...block.matchAll(/(--[\w-]+):\s*(#[0-9a-f]{6})\b/gi)].map((m) => [m[1]!, m[2]!.toLowerCase()]));
}

/** `--name: rgb(r g b / a);` tints of one block. */
export function blockTints(block: string): Map<string, Tint> {
  return new Map(
    [...block.matchAll(/(--[\w-]+):\s*(rgb\(\d+ \d+ \d+ \/ [\d.]+\))/g)].map((m) => [m[1]!, parseTint(m[2]!)!]),
  );
}
