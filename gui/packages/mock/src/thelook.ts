/**
 * The mock's source model: an invented, thelook-shaped e-commerce dataset
 * (`config/relationships/gcp_public_fk_example.yaml`: users ──< orders ──<
 * order_items >── products, products external), plus one invented 200-column
 * table for the wide-table case. Every name and value is generated from
 * syllables here; e-mails are @example.com; ids stay far below 15 digits.
 */
import { cumulative, type Random } from "@synthetic-platform/stats";

export type ColumnKind = "numeric" | "temporal" | "categorical" | "boolean" | "text" | "identifier";
export type BqType = "INT64" | "FLOAT64" | "STRING" | "TIMESTAMP" | "BOOL";
export type Value = string | number | boolean | null;
export type Row = Record<string, Value>;

export interface ColumnDef {
  name: string;
  bqType: BqType;
  kind: ColumnKind;
  role?: "pk" | "fk";
  /** The engine route the profiler would record (source_table_stats.generation_plan). */
  plan: "constant" | "numeric" | "categorical" | "temporal" | "shaped_identifier" | "freetext_llm_pool" | "key";
}

export interface TableDef {
  name: string;
  /** Rows in the source table. */
  sourceRows: number;
  columns: ColumnDef[];
  role: "root" | "driven" | "external" | "isolated";
  /** Generates the i-th source row (joint: later columns may read earlier ones). */
  row(rng: Random, i: number): Row;
}

export interface EdgeDef {
  child: string;
  cols: string[];
  parent: string;
  parentCols: string[];
  drives: boolean;
  external: boolean;
  /** Children per parent in the source: fanout[k] = share of parents with k children (the last bucket is "≥"). */
  fanout: number[];
}

export const PROJECT = "demo-project";
export const SOURCE_DATASET = "synthetic_source";
export const LANDING_DATASET = "synthetic_data";
export const RELATIONSHIP_MODEL = "gcp_public_thelook";

export const sourceFqn = (table: string) => `${PROJECT}.${SOURCE_DATASET}.${table}`;
export const landingFqn = (table: string) => `${PROJECT}.${LANDING_DATASET}.${table}`;

// ------------------------------------------------------------ vocabularies --

const FIRST_A = [
  "Al",
  "Be",
  "Ca",
  "Da",
  "El",
  "Fa",
  "Ga",
  "Ha",
  "Is",
  "Jo",
  "Ka",
  "Le",
  "Ma",
  "Na",
  "Ol",
  "Pa",
  "Ra",
  "Sa",
  "Ta",
  "Va",
  "Wi",
  "Ya",
  "Zo",
];
const FIRST_B = [
  "na",
  "ra",
  "lo",
  "ma",
  "ni",
  "sa",
  "ton",
  "vin",
  "ric",
  "len",
  "die",
  "ssa",
  "mon",
  "lia",
  "bert",
  "ria",
];
const LAST_A = [
  "Mor",
  "Gar",
  "Sil",
  "Hal",
  "Ken",
  "Bre",
  "Cal",
  "Dun",
  "Est",
  "Fer",
  "Gri",
  "Har",
  "Iva",
  "Jor",
  "Kel",
  "Lam",
  "Mar",
  "Nor",
  "Ort",
  "Pel",
  "Qui",
  "Ros",
  "Sal",
  "Tor",
  "Urb",
  "Val",
  "Wes",
  "Yor",
  "Zam",
];
const LAST_B = [
  "ales",
  "ton",
  "son",
  "ez",
  "er",
  "ley",
  "ford",
  "ini",
  "ova",
  "berg",
  "ard",
  "well",
  "ski",
  "ano",
  "ett",
  "man",
  "ero",
];
const CITY_A = [
  "Port",
  "Lake",
  "North",
  "South",
  "East",
  "West",
  "New",
  "Old",
  "Fort",
  "Glen",
  "Mount",
  "Bay",
  "Red",
  "Stone",
  "Green",
  "Silver",
  "Cold",
  "High",
  "Low",
  "Grand",
  "Little",
  "Upper",
  "Lower",
  "Clear",
];
const CITY_B = [
  "Alden",
  "Brook",
  "Cedar",
  "Dale",
  "Elm",
  "Fairview",
  "Grove",
  "Haven",
  "Iris",
  "Juniper",
  "Kestrel",
  "Linden",
  "Maple",
  "Norwood",
  "Oak",
  "Pine",
  "Quarry",
  "Ridge",
  "Spring",
  "Thorn",
  "Vale",
  "Willow",
  "Yarrow",
  "Ash",
  "Birch",
  "Clover",
  "Dunmore",
  "Ember",
  "Fern",
  "Harbor",
];
/** Novel syllables an LLM pool produces (never in the source universe). */
const NOVEL_A = ["Xa", "Qe", "Vy", "Ul", "Ez", "Iv", "Oq", "Yl"];
const NOVEL_B = ["xen", "qor", "vyl", "ush", "zeb", "ith", "oqa", "ylm"];

export const COUNTRIES = [
  "United States",
  "China",
  "Brasil",
  "South Korea",
  "France",
  "United Kingdom",
  "Germany",
  "Spain",
  "Japan",
  "Australia",
  "Belgium",
  "Poland",
  "Colombia",
  "Austria",
];
const COUNTRY_WEIGHTS = [0.22, 0.33, 0.14, 0.05, 0.05, 0.05, 0.04, 0.04, 0.03, 0.02, 0.01, 0.01, 0.005, 0.005];
export const TRAFFIC = ["Search", "Organic", "Facebook", "Email", "Display"];
const TRAFFIC_WEIGHTS = [0.7, 0.15, 0.06, 0.05, 0.04];
export const ORDER_STATUS = ["Complete", "Shipped", "Processing", "Cancelled", "Returned"];
const ORDER_STATUS_WEIGHTS = [0.25, 0.3, 0.2, 0.15, 0.1];
export const CATEGORIES = [
  "Tops",
  "Jeans",
  "Outerwear",
  "Swim",
  "Accessories",
  "Sleep",
  "Active",
  "Socks",
  "Suits",
  "Dresses",
];
export const DEPARTMENTS = ["Women", "Men"];

/** 45 invented regions (≤ 50 distinct, so profiles may show them literally). */
export const STATES = Array.from(
  { length: 45 },
  (_, i) => `${CITY_A[i % 15]} ${["Province", "Region", "State"][i % 3]} ${Math.floor(i / 15) + 1}`,
);

const cartesian = (a: string[], b: string[]) => a.flatMap((x) => b.map((y) => `${x}${y}`));
export const FIRST_NAMES = cartesian(FIRST_A, FIRST_B);
export const LAST_NAMES = cartesian(LAST_A, LAST_B);
export const CITIES = CITY_A.flatMap((a) => CITY_B.map((b) => `${a} ${b}`));
export const NOVEL_FIRST = cartesian(NOVEL_A, FIRST_B);
export const NOVEL_LAST = cartesian(NOVEL_A, LAST_B).concat(cartesian(LAST_A, NOVEL_B));
export const NOVEL_CITIES = NOVEL_A.flatMap((a) => CITY_B.map((b) => `${a}ro ${b}`));

/** Zipf(s) weights over n ranks. */
export function zipf(n: number, s: number): number[] {
  const w = Array.from({ length: n }, (_, i) => 1 / (i + 1) ** s);
  const total = w.reduce((a, b) => a + b, 0);
  return w.map((v) => v / total);
}

export const FIRST_WEIGHTS = zipf(FIRST_NAMES.length, 0.9);
export const LAST_WEIGHTS = zipf(LAST_NAMES.length, 0.7);
export const CITY_WEIGHTS = zipf(CITIES.length, 1.0);
const STATE_WEIGHTS = zipf(STATES.length, 0.6);
const FIRST_CDF = cumulative(FIRST_WEIGHTS);
const LAST_CDF = cumulative(LAST_WEIGHTS);
const CITY_CDF = cumulative(CITY_WEIGHTS);
const STATE_CDF = cumulative(STATE_WEIGHTS);

const DAY = 86_400;
/** 2019-01-01T00:00:00Z and 2026-08-31T00:00:00Z, epoch seconds. */
export const T0 = Date.UTC(2019, 0, 1) / 1000;
export const T1 = Date.UTC(2026, 7, 31) / 1000;

/** A growth-shaped timestamp: density rises over the years, weekday and daytime skew. */
function growthTimestamp(rng: Random): number {
  const u = rng.uniform() ** 0.6;
  let t = T0 + (T1 - T0) * u;
  const day = Math.floor(t / DAY) * DAY;
  const hour = rng.weighted([1, 1, 1, 1, 1, 2, 3, 5, 7, 8, 9, 9, 9, 9, 8, 8, 8, 9, 10, 10, 8, 6, 4, 2]);
  t = day + hour * 3600 + rng.int(0, 3599);
  return t;
}

export function isoFromEpoch(seconds: number): string {
  return new Date(Math.round(seconds) * 1000).toISOString().replace(".000Z", "Z");
}

export function email(first: string, last: string, suffix: number | null): string {
  return `${first}.${last}${suffix === null ? "" : suffix}@example.com`.toLowerCase();
}

// ---------------------------------------------------------------- tables --

const col = (
  name: string,
  bqType: BqType,
  kind: ColumnKind,
  plan: ColumnDef["plan"],
  role?: ColumnDef["role"],
): ColumnDef => (role ? { name, bqType, kind, plan, role } : { name, bqType, kind, plan });

export const users: TableDef = {
  name: "users",
  sourceRows: 100_000,
  role: "root",
  columns: [
    col("id", "INT64", "identifier", "key", "pk"),
    col("first_name", "STRING", "text", "freetext_llm_pool"),
    col("last_name", "STRING", "text", "freetext_llm_pool"),
    col("email", "STRING", "identifier", "shaped_identifier"),
    col("age", "INT64", "numeric", "numeric"),
    col("gender", "STRING", "categorical", "categorical"),
    col("state", "STRING", "categorical", "categorical"),
    col("city", "STRING", "text", "freetext_llm_pool"),
    col("country", "STRING", "categorical", "categorical"),
    col("traffic_source", "STRING", "categorical", "categorical"),
    col("created_at", "TIMESTAMP", "temporal", "temporal"),
  ],
  row(rng, i) {
    const first = FIRST_NAMES[rng.fromCdf(FIRST_CDF)]!;
    const last = LAST_NAMES[rng.fromCdf(LAST_CDF)]!;
    const country = COUNTRIES[rng.weighted(COUNTRY_WEIGHTS)]!;
    return {
      id: i + 1,
      first_name: first,
      last_name: last,
      email: email(first, last, rng.bernoulli(0.8) ? rng.int(1, 9999) : null),
      age: rng.int(12, 70),
      gender: rng.bernoulli(0.5) ? "F" : "M",
      state: rng.bernoulli(0.01) ? null : STATES[rng.fromCdf(STATE_CDF)]!,
      city: rng.bernoulli(0.03) ? "" : CITIES[rng.fromCdf(CITY_CDF)]!,
      country,
      // Traffic depends a little on the country (a weak categorical pair).
      traffic_source: TRAFFIC[rng.weighted(country === "China" ? [0.5, 0.3, 0.1, 0.05, 0.05] : TRAFFIC_WEIGHTS)]!,
      created_at: isoFromEpoch(growthTimestamp(rng)),
    };
  },
};

export const orders: TableDef = {
  name: "orders",
  sourceRows: 125_000,
  role: "driven",
  columns: [
    col("order_id", "INT64", "identifier", "key", "pk"),
    col("user_id", "INT64", "identifier", "key", "fk"),
    col("status", "STRING", "categorical", "categorical"),
    col("created_at", "TIMESTAMP", "temporal", "temporal"),
    col("shipped_at", "TIMESTAMP", "temporal", "temporal"),
    col("delivered_at", "TIMESTAMP", "temporal", "temporal"),
    col("num_of_item", "INT64", "numeric", "numeric"),
  ],
  row(rng, i) {
    const status = ORDER_STATUS[rng.weighted(ORDER_STATUS_WEIGHTS)]!;
    const created = growthTimestamp(rng);
    const shipped = ["Shipped", "Complete", "Returned"].includes(status) ? created + rng.between(0.5, 72) * 3600 : null;
    const delivered = shipped !== null && status !== "Shipped" ? shipped + rng.between(1, 5) * DAY : null;
    // Items per order also depend on the status (returns skew to bigger baskets).
    const items = 1 + rng.weighted(status === "Returned" ? [0.5, 0.3, 0.12, 0.08] : [0.72, 0.19, 0.06, 0.03]);
    return {
      order_id: i + 1,
      user_id: 1 + ((i * 7919) % users.sourceRows),
      status,
      created_at: isoFromEpoch(created),
      shipped_at: shipped === null ? null : isoFromEpoch(shipped),
      delivered_at: delivered === null ? null : isoFromEpoch(delivered),
      num_of_item: items,
    };
  },
};

export const orderItems: TableDef = {
  name: "order_items",
  sourceRows: 181_000,
  role: "driven",
  columns: [
    col("id", "INT64", "identifier", "key", "pk"),
    col("order_id", "INT64", "identifier", "key", "fk"),
    col("user_id", "INT64", "identifier", "key", "fk"),
    col("product_id", "INT64", "identifier", "key", "fk"),
    col("status", "STRING", "categorical", "categorical"),
    col("created_at", "TIMESTAMP", "temporal", "temporal"),
    col("sale_price", "FLOAT64", "numeric", "numeric"),
  ],
  row(rng, i) {
    const status = ORDER_STATUS[rng.weighted(ORDER_STATUS_WEIGHTS)]!;
    const price = Math.min(999, Math.max(0.02, Math.exp(rng.normal(3.5, 0.9))));
    return {
      id: i + 1,
      order_id: 1 + ((i * 104_729) % orders.sourceRows),
      user_id: 1 + ((i * 7919) % users.sourceRows),
      product_id: 1 + ((i * 31) % products.sourceRows),
      status,
      created_at: isoFromEpoch(growthTimestamp(rng)),
      // Cancelled lines are cheaper on average (a numeric ↔ categorical pair).
      sale_price: Math.round((status === "Cancelled" ? price * 0.7 : price) * 100) / 100,
    };
  },
};

export const products: TableDef = {
  name: "products",
  sourceRows: 29_120,
  role: "external",
  columns: [
    col("id", "INT64", "identifier", "key", "pk"),
    col("category", "STRING", "categorical", "categorical"),
    col("brand", "STRING", "text", "freetext_llm_pool"),
    col("department", "STRING", "categorical", "categorical"),
    col("retail_price", "FLOAT64", "numeric", "numeric"),
    col("cost", "FLOAT64", "numeric", "numeric"),
  ],
  row(rng, i) {
    const retail = Math.round(Math.min(999, Math.max(0.5, Math.exp(rng.normal(3.6, 0.8)))) * 100) / 100;
    return {
      id: i + 1,
      category: CATEGORIES[rng.int(0, CATEGORIES.length - 1)]!,
      brand: `${LAST_A[rng.int(0, LAST_A.length - 1)]}${["co", "wear", "line", "works"][rng.int(0, 3)]}`,
      department: DEPARTMENTS[rng.int(0, 1)]!,
      retail_price: retail,
      cost: Math.round(retail * rng.between(0.35, 0.6) * 100) / 100,
    };
  },
};

/** The invented 200-column table (isolated: no relationship model). */
export const WIDE_COLUMNS = 200;
export const userFeatures: TableDef = {
  name: "user_features",
  sourceRows: 250_000,
  role: "isolated",
  columns: Array.from({ length: WIDE_COLUMNS }, (_, j): ColumnDef => {
    const name = `feat_${String(j + 1).padStart(3, "0")}`;
    if (j % 10 < 6) return col(name, "FLOAT64", "numeric", "numeric");
    if (j % 10 < 9) return col(name, "STRING", "categorical", "categorical");
    return col(name, "BOOL", "boolean", "categorical");
  }),
  row(rng) {
    const out: Row = {};
    this.columns.forEach((c, j) => {
      if (c.kind === "numeric")
        out[c.name] = rng.bernoulli(0.02) ? null : Math.round(rng.normal(j % 7, 1 + (j % 3)) * 1000) / 1000;
      else if (c.kind === "categorical") out[c.name] = `L${rng.weighted([0.4, 0.25, 0.15, 0.1, 0.06, 0.04])}`;
      else out[c.name] = rng.bernoulli(0.3 + (j % 5) * 0.1);
    });
    return out;
  },
};

export const THELOOK_TABLES = [users, orders, orderItems, products];
export const TABLES: Record<string, TableDef> = Object.fromEntries(
  [...THELOOK_TABLES, userFeatures].map((t) => [t.name, t]),
);

/** Fan-out shares from the model (users → orders → order_items). */
export const EDGES: EdgeDef[] = [
  {
    child: "orders",
    cols: ["user_id"],
    parent: "users",
    parentCols: ["id"],
    drives: true,
    external: false,
    fanout: [0.32, 0.33, 0.19, 0.1, 0.06],
  },
  {
    child: "order_items",
    cols: ["order_id", "user_id"],
    parent: "orders",
    parentCols: ["order_id", "user_id"],
    drives: true,
    external: false,
    fanout: [0, 0.69, 0.2, 0.07, 0.04],
  },
  {
    child: "order_items",
    cols: ["user_id"],
    parent: "users",
    parentCols: ["id"],
    drives: false,
    external: false,
    fanout: [0.4, 0.22, 0.15, 0.1, 0.13],
  },
  {
    child: "order_items",
    cols: ["product_id"],
    parent: "products",
    parentCols: ["id"],
    drives: false,
    external: true,
    fanout: [0.05, 0.2, 0.3, 0.25, 0.2],
  },
];

export const edgeLabel = (e: EdgeDef) => `${e.child}.${e.cols.join("+")}->${e.parent}.${e.parentCols.join("+")}`;
