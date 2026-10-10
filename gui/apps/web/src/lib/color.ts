import { readToken } from "./theme";

export type RGB = [number, number, number];
export type RGBA = [number, number, number, number];

/** "#3987e5" → [57, 135, 229]. Accepts 3- and 6-digit hex; anything else is mid grey. */
export function hexToRgb(hex: string): RGB {
  const clean = hex.trim().replace(/^#/, "");
  const full = clean.length === 3 ? [...clean].map((c) => c + c).join("") : clean;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return [128, 128, 128];
  const value = Number.parseInt(full, 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

/** A token as an RGBA array for deck.gl layers (`getFillColor`). Re-read it after a theme change. */
export function tokenRgba(name: `--${string}`, alpha = 255): RGBA {
  const [r, g, b] = hexToRgb(readToken(name, "#808080"));
  return [r, g, b, alpha];
}
