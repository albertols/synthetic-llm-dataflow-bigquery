/**
 * The route validators (`@/lib/search`) against the zod/mini schemas they
 * replaced: the same seeded inputs, the same validated search, for every
 * route. zod stays a test-only oracle here; the shell no longer bundles it.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod/mini";

import { mulberry32 } from "@synthetic-platform/stats";

import * as config from "@/features/config/route";
import * as evaluation from "@/features/evaluation/route";
import * as intro from "@/features/intro/route";
import * as rag from "@/features/rag/route";

import { extend, finite, integer, list, oneOf, searchParams, text, withDefault } from "./search";

// ------------------------------------------------ the zod/mini schemas, verbatim --

const introZod = z.object({
  q: z.catch(z.optional(z.string().check(z.maxLength(120))), undefined),
  ns: z.catch(z.optional(z.string().check(z.regex(/^[a-z][a-z0-9_-]{0,23}$/))), undefined),
});

const ragString = z.catch(z.optional(z.string().check(z.maxLength(200))), undefined);
const ragZod = z.object({
  table: ragString,
  digest: ragString,
  embedder: ragString,
  space: ragString,
  strategy: z.catch(z.optional(z.enum(rag.strategyIds)), undefined),
  k: z.catch(z.optional(z.int().check(z.minimum(1), z.maximum(16))), undefined),
  attempt: z.catch(z.optional(z.int().check(z.minimum(0), z.maximum(7))), undefined),
  proj: z.catch(z.optional(z.enum(rag.projections)), undefined),
  view: z.catch(z.optional(z.enum(rag.views)), undefined),
  color: z.catch(z.optional(z.enum(rag.colorBys)), undefined),
  model: ragString,
});

const cfgString = z.catch(z.optional(z.string().check(z.maxLength(400))), undefined);
const cfgNumber = z.catch(z.optional(z.number()), undefined);
const cfgBoolean = z.catch(z.optional(z.boolean()), undefined);
const configZod = z.object({
  section: z.catch(z.optional(z.enum(config.CONFIG_SECTIONS)), undefined),
  knob: cfgString,
  scenario: cfgString,
  table: cfgString,
  digest: cfgString,
  snapshot: cfgString,
  column: cfgString,
  N: cfgNumber,
  M: cfgNumber,
  n: cfgNumber,
  p: cfgNumber,
  q: cfgNumber,
  tier: cfgString,
  uniq: cfgString,
  env: cfgString,
  K: cfgNumber,
  D: cfgNumber,
  s: cfgNumber,
  sdk: cfgString,
  warm: cfgBoolean,
  filter: cfgBoolean,
});

const optString = () => z.catch(z.optional(z.string().check(z.maxLength(200))), undefined);
const optNumber = () => z.catch(z.optional(z.number()), undefined);
const stringList = () =>
  z.catch(z.optional(z.array(z.string().check(z.maxLength(300))).check(z.maxLength(50))), undefined);
const numberList = () => z.catch(z.optional(z.array(z.number()).check(z.maxLength(50))), undefined);
const listZod = z.object({
  q: optString(),
  status: stringList(),
  engine: stringList(),
  llm_model: stringList(),
  embedder: stringList(),
  retrieval: stringList(),
  seed: stringList(),
  tables: stringList(),
  env: stringList(),
  trigger: stringList(),
  runner: stringList(),
  mode: stringList(),
  source_stats_tier: stringList(),
  profiler_version: stringList(),
  evaluator_version: stringList(),
  catalogue_version: stringList(),
  relationship_model: stringList(),
  reference_rows_limit: numberList(),
  num_rows: numberList(),
  similarity_min: optNumber(),
  similarity_max: optNumber(),
  from: optString(),
  to: optString(),
  sort: z.catch(z.optional(z.enum(evaluation.listSortKeys)), undefined),
  order: z.catch(z.optional(z.enum(["asc", "desc"])), undefined),
  page: z.catch(z.optional(z.int().check(z.minimum(0), z.maximum(10_000))), undefined),
  pick: stringList(),
});
const compareZod = z.extend(listZod, {
  ids: z.catch(z._default(z.array(z.string()).check(z.maxLength(12)), []), []),
  color: z.catch(z.optional(z.enum(["engine", "model"])), undefined),
  a: optString(),
  b: optString(),
  rows: z.catch(z.optional(z.enum(["changed", "all", "not_comparable"])), undefined),
  family: z.catch(z.optional(z.enum(["overall", "fidelity", "privacy", "integrity", "diversity"])), undefined),
});
const runZod = z.object({
  tab: z.catch(z.optional(z.enum(evaluation.runTabs)), undefined),
  column: optString(),
  table: optString(),
  family: z.catch(z.optional(z.enum(["fidelity", "privacy", "integrity", "diversity"])), undefined),
  problems: z.catch(z.optional(z.boolean()), undefined),
  q: optString(),
  rows: z.catch(z.optional(z.int().check(z.minimum(1), z.maximum(5000))), undefined),
});

// --------------------------------------------------------------- the inputs --

/** Values TanStack Router's JSON search parser can hand a route, plus each schema's own vocabulary. */
const BASE_VALUES: unknown[] = [
  undefined,
  null,
  "",
  "users",
  "a".repeat(120),
  "a".repeat(121),
  "a".repeat(200),
  "a".repeat(201),
  "a".repeat(300),
  "a".repeat(301),
  "a".repeat(400),
  "a".repeat(401),
  "metric",
  "Metric",
  "9lives",
  "a".repeat(24),
  "a".repeat(25),
  0,
  -1,
  1,
  3,
  7,
  8,
  16,
  17,
  1.5,
  5000,
  5001,
  10_000,
  10_001,
  2 ** 53,
  1e21,
  true,
  false,
  [],
  ["a"],
  ["a", "b"],
  [1, 2],
  ["a", 1],
  ["x".repeat(301)],
  Array.from({ length: 12 }, (_, i) => `e${i}`),
  Array.from({ length: 13 }, (_, i) => `e${i}`),
  Array.from({ length: 50 }, (_, i) => i),
  Array.from({ length: 51 }, (_, i) => i),
  {},
  { a: 1 },
  // Code points vs UTF-16 units: zod 4 counts "🎁" (two units) as one.
  "🎁".repeat(120),
  "🎁".repeat(121),
  "🎁".repeat(200),
  "a".repeat(119) + "🎁",
  "\ud83c".repeat(121),
];

function vocabulary(): unknown[] {
  return [
    ...rag.strategyIds,
    ...rag.projections,
    ...rag.views,
    ...rag.colorBys,
    ...config.CONFIG_SECTIONS,
    ...evaluation.listSortKeys,
    ...evaluation.runTabs,
    "asc",
    "desc",
    "engine",
    "model",
    "changed",
    "all",
    "not_comparable",
    "overall",
    "fidelity",
    "privacy",
    "integrity",
    "diversity",
  ];
}

const VALUES = [...BASE_VALUES, ...vocabulary()];

function inputs(keys: readonly string[], seed: number, count = 400): Record<string, unknown>[] {
  const rand = mulberry32(seed);
  const pick = () => VALUES[Math.floor(rand() * VALUES.length)];
  const out: Record<string, unknown>[] = [{}];
  // Every value in every key at least once…
  for (const key of keys) for (const value of VALUES) out.push({ [key]: value });
  // …then random mixes, with keys the schema does not know.
  for (let i = 0; i < count; i += 1) {
    const input: Record<string, unknown> = { unknown: pick(), extra: "x" };
    for (const key of keys) if (rand() < 0.6) input[key] = pick();
    out.push(input);
  }
  return out;
}

type Validate = (input: never) => unknown;

describe("route search validators match the zod/mini schemas they replaced", () => {
  const cases: Array<[string, { parse: (input: unknown) => unknown }, Validate, readonly string[]]> = [
    ["intro", introZod, intro.searchSchema, Object.keys(introZod.def.shape)],
    ["rag", ragZod, rag.searchSchema, Object.keys(ragZod.def.shape)],
    ["config", configZod, config.searchSchema, Object.keys(configZod.def.shape)],
    ["evaluation list", listZod, evaluation.listSearchSchema, Object.keys(listZod.def.shape)],
    ["evaluation compare", compareZod, evaluation.compareSearchSchema, Object.keys(compareZod.def.shape)],
    ["evaluation run", runZod, evaluation.runSearchSchema, Object.keys(runZod.def.shape)],
  ];

  it.each(cases)("%s", (_, schema, validate, keys) => {
    for (const input of inputs(keys, keys.length)) {
      const expected = schema.parse(input) as Record<string, unknown>;
      const actual = (validate as (input: unknown) => Record<string, unknown>)(input);
      // Fields added after the zod era (none are in `keys`) are compared by their own tests.
      const shared = Object.fromEntries(Object.entries(actual).filter(([key]) => keys.includes(key)));
      expect(shared, JSON.stringify(input)).toEqual(expected);
    }
  });
});

describe("the helpers", () => {
  it("drop unknown keys and keep defaults", () => {
    const validate = searchParams({ q: text({ max: 3 }), ids: withDefault(list(text(), 2), () => [] as string[]) });
    expect(validate({ q: "abc", other: 1 } as never)).toEqual({ q: "abc", ids: [] });
    expect(validate({ q: "abcd", ids: ["x"] } as never)).toEqual({ ids: ["x"] });
    expect(validate({ ids: ["x", "y", "z"] } as never)).toEqual({ ids: [] });
  });

  it("text({ max }) counts code points, like zod 4", () => {
    const three = text({ max: 3 });
    expect(three("🎁🎁🎁")).toBe("🎁🎁🎁"); // six UTF-16 units, three code points
    expect(three("🎁🎁🎁🎁")).toBeUndefined();
    expect(three("a👨‍👩‍👧")).toBeUndefined(); // a ZWJ family is five code points
    expect(three("\ud83c\ud83c\ud83c")).toBe("\ud83c\ud83c\ud83c"); // lone surrogates count one each
    const zodThree = z.string().check(z.maxLength(3));
    for (const v of ["🎁🎁🎁", "🎁🎁🎁🎁", "a👨‍👩‍👧", "\ud83c\ud83c\ud83c", "abc", "abcd"])
      expect(three(v) !== undefined, JSON.stringify(v)).toBe(zodThree.safeParse(v).success);
  });

  it("text({ pattern }) gives the same answer on every call, even for a /g or /y pattern", () => {
    for (const pattern of [/^a+$/g, /a/y]) {
      const field = text({ pattern });
      expect([field("aaa"), field("aaa"), field("aaa")]).toEqual(["aaa", "aaa", "aaa"]);
      expect(field("b")).toBeUndefined();
      expect(field("aaa")).toBe("aaa");
    }
  });

  it("the CONFIG channel param takes a channel id and nothing else", () => {
    const channel = (value: unknown) => config.searchSchema({ channel: value } as never).channel;
    expect(channel("free_text")).toBe("free_text");
    for (const bad of ["Free_Text", "free-text", "1sampling", "_x", "", "a".repeat(41), "ok ", 3, ["free_text"], null])
      expect(channel(bad), JSON.stringify(bad)).toBeUndefined();
    expect(channel("a".repeat(40))).toBe("a".repeat(40));
  });

  it("extend overrides and adds fields", () => {
    const base = searchParams({ a: text(), b: finite() });
    const more = extend(base, { b: integer({ min: 0, max: 1 }), c: oneOf(["x"]) });
    expect(more({ a: "s", b: 0.5, c: "x" } as never)).toEqual({ a: "s", c: "x" });
    expect(more({ b: 1 } as never)).toEqual({ b: 1 });
  });
});
