import { createContext, useContext, type ReactNode } from "react";

/**
 * Extra content a tab adds to its (i) popovers, by concept id: EVALUATION
 * links a metric to the CONFIG knob that drives it. Provide it around a page;
 * every InfoHint inside (ChartFrame's too) renders what it returns for its
 * concept after the concept's own links. Popovers render in a portal, and
 * React context follows the component tree, not the DOM, so it reaches them.
 */
export type ConceptExtras = (conceptId: string) => ReactNode;

export const ConceptExtrasContext = createContext<ConceptExtras | null>(null);

/** What the nearest provider adds for `conceptId` (null without one). */
export function useConceptExtras(conceptId: string): ReactNode {
  return useContext(ConceptExtrasContext)?.(conceptId) ?? null;
}
