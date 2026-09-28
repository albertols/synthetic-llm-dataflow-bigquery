/**
 * Search-param validators for the route modules.
 *
 * Route modules sit in the shell chunk, and zod — even `zod/mini` — cost the
 * shell about 13 kB gzip for what is a handful of type checks. These helpers
 * do the same checks with the same "catch" semantics the zod schemas had:
 * a value of the wrong type, too long, or outside its set becomes undefined
 * (or the field's default), never an error page. Unknown keys are dropped.
 *
 * TanStack Router JSON-parses every query value first, so `?k=3` arrives as
 * the number 3 and `?q=123` as a number too: a `text` field then drops it,
 * exactly as `z.string()` did.
 *
 *   export const searchSchema = searchParams({
 *     q: text({ max: 120 }),
 *     k: integer({ min: 1, max: 16 }),
 *     strategy: oneOf(["centroid", "kcenter"]),
 *   });
 *   export type RagSearch = SearchOf<typeof searchSchema>;
 *
 * `lib/search.test.ts` runs every route's schema against the zod/mini schema it
 * replaced on the same inputs.
 */
import type { SearchSchemaInput } from "@tanstack/react-router";

/** One field: an unknown query value → the typed value, or undefined when it does not pass. */
export type Field<T> = (value: unknown) => T;
type Fields = Record<string, Field<unknown>>;

type OptionalKeys<S extends Fields> = { [K in keyof S]: undefined extends ReturnType<S[K]> ? K : never }[keyof S];

/** What a page reads (`useSearch()`): defaulted fields are always present, the rest optional. */
export type SearchOutput<S extends Fields> = {
  [K in Exclude<keyof S, OptionalKeys<S>>]: ReturnType<S[K]>;
} & { [K in OptionalKeys<S>]?: ReturnType<S[K]> };

/** What a link may pass (`<Link search={…}>`): every field optional. */
export type SearchInput<S extends Fields> = { [K in keyof S]?: Exclude<ReturnType<S[K]>, undefined> };

export type SearchValidator<S extends Fields> = ((input: SearchInput<S> & SearchSchemaInput) => SearchOutput<S>) & {
  /** The fields, for `extend`. */
  readonly fields: S;
};

/** The validated search a validator produces. */
export type SearchOf<V> = V extends SearchValidator<infer S> ? SearchOutput<S> : never;

/** A route's `validateSearch`: runs each field, keeps the ones that pass, drops unknown keys. */
export function searchParams<S extends Fields>(fields: S): SearchValidator<S> {
  const validate = (input: SearchInput<S> & SearchSchemaInput): SearchOutput<S> => {
    const raw = input as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(fields)) {
      const value = fields[key]!(raw[key]);
      if (value !== undefined) out[key] = value;
    }
    return out as SearchOutput<S>;
  };
  return Object.assign(validate, { fields });
}

/** Another validator's fields plus (or overriding) these. */
export function extend<A extends Fields, B extends Fields>(base: SearchValidator<A>, fields: B) {
  return searchParams<Omit<A, keyof B> & B>({ ...base.fields, ...fields });
}

/** A string of at most `max` characters, optionally matching `pattern` (`z.string().check(z.maxLength, z.regex)`). */
export function text({ max, pattern }: { max?: number; pattern?: RegExp } = {}): Field<string | undefined> {
  return (value) =>
    typeof value === "string" && (max === undefined || value.length <= max) && (!pattern || pattern.test(value))
      ? value
      : undefined;
}

/** One of `values` (`z.enum`). */
export function oneOf<const T extends readonly string[]>(values: T): Field<T[number] | undefined> {
  return (value) => (typeof value === "string" && values.includes(value) ? value : undefined);
}

/** A safe integer in [min, max] (`z.int().check(z.minimum, z.maximum)`). */
export function integer({ min, max }: { min: number; max: number }): Field<number | undefined> {
  return (value) =>
    typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max ? value : undefined;
}

/** A finite number (`z.number()`). */
export function finite(): Field<number | undefined> {
  return (value) => (typeof value === "number" && Number.isFinite(value) ? value : undefined);
}

/** `true` or `false` (`z.boolean()`). */
export function flag(): Field<boolean | undefined> {
  return (value) => (typeof value === "boolean" ? value : undefined);
}

/** An array of at most `maxItems`, every item passing `item`; one bad item drops the whole list. */
export function list<T>(item: Field<T | undefined>, maxItems: number): Field<T[] | undefined> {
  return (value) => {
    if (!Array.isArray(value) || value.length > maxItems) return undefined;
    const out: T[] = [];
    for (const entry of value) {
      const parsed = item(entry);
      if (parsed === undefined) return undefined;
      out.push(parsed);
    }
    return out;
  };
}

/** `field`, or `fallback` when the value is missing or does not pass (`z.catch(z._default(…))`). */
export function withDefault<T>(field: Field<T | undefined>, fallback: () => T): Field<T> {
  return (value) => field(value) ?? fallback();
}
