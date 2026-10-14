/**
 * Reading a stored `row_doc` back into its clauses.
 *
 * `rag_chunks` keeps the GReaT sentence ("col is value, col is value, …"),
 * not the row (and never `source_pk`), so the lab parses the sentence. Values
 * are not escaped by `serialize_row`: a value may itself contain ", " or
 * " is ". The column sequence is therefore learned first — the parse most
 * row docs agree on — and each row is then split at exactly the known
 * `", <next column> is "` boundaries, which is unambiguous unless a value
 * contains its own next column's name.
 */
export interface Clause {
  column: string;
  value: string;
}

/** A naive split: at ", " then at the first " is ". Fine for learning the column names. */
export function naiveClauses(text: string): Clause[] {
  const clauses: Clause[] = [];
  for (const piece of text.split(", ")) {
    const at = piece.indexOf(" is ");
    if (at > 0 && /^[A-Za-z_][A-Za-z0-9_]*$/.test(piece.slice(0, at)))
      clauses.push({ column: piece.slice(0, at), value: piece.slice(at + 4) });
    else if (clauses.length) clauses[clauses.length - 1]!.value += `, ${piece}`;
  }
  return clauses;
}

/** The column sequence most texts parse to (null when no text parses). */
export function learnColumns(texts: readonly string[], sample = 64): string[] | null {
  const counts = new Map<string, number>();
  for (const text of texts.slice(0, sample)) {
    const key = naiveClauses(text)
      .map((c) => c.column)
      .join("\u0000");
    if (key) counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  let best: string | null = null;
  let bestCount = 0;
  for (const [key, count] of counts)
    if (count > bestCount) {
      best = key;
      bestCount = count;
    }
  return best ? best.split("\u0000") : null;
}

/** Split one sentence at the known column boundaries; null when it does not follow the sequence. */
export function parseWithColumns(text: string, columns: readonly string[]): Clause[] | null {
  if (!columns.length) return null;
  const clauses: Clause[] = [];
  let cursor = 0;
  for (let i = 0; i < columns.length; i += 1) {
    const head = `${i === 0 ? "" : ", "}${columns[i]} is `;
    if (text.slice(cursor, cursor + head.length) !== head) return null;
    const start = cursor + head.length;
    const nextHead = i + 1 < columns.length ? `, ${columns[i + 1]} is ` : null;
    const end = nextHead ? text.indexOf(nextHead, start) : text.length;
    if (end < 0) return null;
    clauses.push({ column: columns[i]!, value: text.slice(start, end) });
    cursor = end;
  }
  return cursor === text.length ? clauses : null;
}

/** Parse with the learned sequence, falling back to the naive split. */
export function parseGreat(text: string, columns: readonly string[] | null): Clause[] {
  return (columns && parseWithColumns(text, columns)) ?? naiveClauses(text);
}

/**
 * The values of one column across row docs, first-seen distinct and non-empty,
 * in row order — what the pool branch embeds for its seeds (rung 2: the
 * column's own values from the first 1,024 reference rows). "null" is how
 * `serialize_row` writes a missing value, so it is skipped like None.
 */
export function distinctColumnValues(texts: readonly string[], columns: readonly string[] | null, column: string) {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const text of texts) {
    const clause = parseGreat(text, columns).find((c) => c.column === column);
    if (!clause || clause.value === "" || clause.value === "null" || seen.has(clause.value)) continue;
    seen.add(clause.value);
    out.push(clause.value);
  }
  return out;
}
