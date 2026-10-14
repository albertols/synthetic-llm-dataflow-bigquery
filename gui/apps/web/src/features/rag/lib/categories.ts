/**
 * Colour-by categories for the point cloud.
 *
 * A point cloud is an all-pairs form (any two marks can touch), so only the
 * first three categorical slots are used (BRANDING.md, `--pairs all`); later
 * categories fold into "Other". Slots follow the category's fixed order —
 * kinds as the schema lists them, columns in the set's order, tables in the
 * BFF's order, clusters by first appearance — never its size, so a filter
 * never repaints a survivor.
 */
import type { Cloud } from "./useCloud";

export const COLOR_BY = ["kind", "column", "table", "cluster"] as const;
export type ColorBy = (typeof COLOR_BY)[number];

export const COLOR_BY_LABELS: Record<ColorBy, string> = {
  kind: "Chunk kind",
  column: "Column",
  table: "Table",
  cluster: "Cluster (384-d)",
};

export const MAX_SLOTS = 3;

export interface Category {
  key: string;
  label: string;
  count: number;
  /** 0–2 for `--chart-1..3`; null folds into "Other". */
  slot: number | null;
}

export interface Categorised {
  categories: Category[];
  /** Per point: index into `categories`. */
  of: Int32Array;
}

const KIND_LABELS: Record<string, string> = { row_doc: "Row document", free_text_col: "Value chunk" };

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

export function categorise(
  cloud: Cloud,
  by: ColorBy,
  options: { columns?: readonly string[]; tables?: readonly string[]; clusters?: Int32Array | null } = {},
): Categorised {
  const keyOf = (i: number): string => {
    const m = cloud.meta[i]!;
    switch (by) {
      case "kind":
        return m.chunk_kind;
      case "column":
        return m.chunk_kind === "row_doc" ? "__row__" : (m.column ?? "__none__");
      case "table":
        return cloud.tables[i]!;
      case "cluster": {
        const label = options.clusters?.[i];
        return label === undefined || label < 0 ? "__pending__" : String(label);
      }
    }
  };
  const order: string[] =
    by === "kind"
      ? ["row_doc", "free_text_col"]
      : by === "column"
        ? ["__row__", ...(options.columns ?? [])]
        : by === "table"
          ? [...(options.tables ?? [])]
          : [];
  const counts = new Map<string, number>();
  const of = new Int32Array(cloud.n);
  const keys: string[] = new Array<string>(cloud.n);
  for (let i = 0; i < cloud.n; i += 1) {
    const key = keyOf(i);
    keys[i] = key;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const seen = [...counts.keys()];
  const ordered = [
    ...order.filter((k) => counts.has(k)),
    ...seen.filter((k) => !order.includes(k)).sort((a, b) => (by === "cluster" ? Number(a) - Number(b) : 0)),
  ];
  const index = new Map(ordered.map((k, i) => [k, i]));
  keys.forEach((key, i) => (of[i] = index.get(key)!));
  const categories = ordered.map((key, i) => ({
    key,
    label: labelFor(by, key),
    count: counts.get(key)!,
    slot: i < MAX_SLOTS ? i : null,
  }));
  return { categories, of };
}

function labelFor(by: ColorBy, key: string): string {
  if (by === "kind") return kindLabel(key);
  if (key === "__row__") return "Row document";
  if (key === "__none__") return "Value chunk (no column)";
  if (key === "__pending__") return "Clustering…";
  if (by === "cluster") return `Cluster ${Number(key) + 1}`;
  return key;
}
