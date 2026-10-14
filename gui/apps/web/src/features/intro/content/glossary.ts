/**
 * Glossary search over the concept registry — the same entries every (i)
 * InfoHint shows. Pure: ranks concepts for a query and a namespace.
 */
import type { Concept } from "@contracts/concept";

/** Reader-facing names for the concept namespaces (see conceptNamespaces in the contracts). */
export const NAMESPACE_LABELS: Record<string, string> = {
  core: "Foundation",
  metric: "Metrics",
  intro: "Pipeline & design",
  eval: "Evaluation",
  rag: "RAG",
  knob: "Knobs",
  config: "Config",
  stats: "Source stats",
};

export function namespaceOf(concept: Pick<Concept, "id">): string {
  return concept.id.split(":")[0] ?? "";
}

export function namespaceLabel(namespace: string): string {
  return NAMESPACE_LABELS[namespace] ?? namespace;
}

/** Lower case, accents stripped, punctuation folded to spaces. */
export function fold(text: string): string {
  return text
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .replace(/[_\-.:/()]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

type Indexed = { concept: Concept; title: string; id: string; body: string };

export function indexConcepts(concepts: readonly Concept[]): Indexed[] {
  return concepts.map((concept) => ({
    concept,
    title: fold(concept.title),
    id: fold(concept.id),
    body: fold(
      [
        concept.purpose,
        concept.pitfalls,
        concept.interpretation?.good,
        concept.interpretation?.bad,
        concept.interpretation?.tip,
      ]
        .filter(Boolean)
        .join(" "),
    ),
  }));
}

/** 0 = best. Title matches beat id matches beat body matches; null = no match. */
function termRank(entry: Indexed, term: string): number | null {
  if (entry.title === term) return 0;
  if (entry.title.startsWith(term)) return 1;
  if (entry.title.split(" ").some((word) => word.startsWith(term))) return 2;
  if (entry.title.includes(term)) return 3;
  if (entry.id.includes(term)) return 4;
  if (entry.body.includes(term)) return 6;
  return null;
}

/**
 * Concepts matching every term of `query` (in title, id or text), best
 * first; all of them, by title, for an empty query. `namespace` narrows to
 * one namespace ("all" or undefined for every one).
 */
export function searchConcepts(index: readonly Indexed[], query: string, namespace?: string): Concept[] {
  const terms = fold(query).split(" ").filter(Boolean);
  const scoped = namespace && namespace !== "all" ? index.filter((e) => namespaceOf(e.concept) === namespace) : index;
  if (!terms.length) return [...scoped].sort((a, b) => a.title.localeCompare(b.title)).map((e) => e.concept);
  const hits: Array<{ entry: Indexed; score: number }> = [];
  for (const entry of scoped) {
    let score = 0;
    let matched = true;
    for (const term of terms) {
      const rank = termRank(entry, term);
      if (rank === null) {
        matched = false;
        break;
      }
      score += rank;
    }
    if (matched) hits.push({ entry, score });
  }
  return hits
    .sort((a, b) => a.score - b.score || a.entry.title.localeCompare(b.entry.title))
    .map((hit) => hit.entry.concept);
}
