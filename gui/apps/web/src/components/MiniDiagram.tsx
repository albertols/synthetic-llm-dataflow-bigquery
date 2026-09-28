/**
 * MiniDiagram — small explanatory SVGs shown inside InfoHints and cards.
 *
 * Registry, merged with `import.meta.glob` and loaded lazily per id:
 *   src/components/diagrams/<name>.tsx        → id "core:<name>"      (foundation)
 *   src/features/<tab>/diagrams/<name>.tsx    → id "<tab>:<name>"     (each tab)
 * A diagram module default-exports a component taking `DiagramProps` and
 * renders one `<svg role="img">` with a `<title>`; colours come from tokens
 * (`var(--chart-1)`, `var(--text-2)` …), never raw hex.
 */
import { lazy, Suspense, type ComponentType, type LazyExoticComponent } from "react";

import { cn } from "@/lib/cn";

export type DiagramProps = { className?: string };
type DiagramModule = { default: ComponentType<DiagramProps> };

const coreModules = import.meta.glob<DiagramModule>(["./diagrams/*.tsx", "!./diagrams/*.test.tsx"]);
const featureModules = import.meta.glob<DiagramModule>([
  "../features/*/diagrams/*.tsx",
  "!../features/*/diagrams/*.test.tsx",
]);

function idFor(path: string): string | undefined {
  const core = /^\.\/diagrams\/([^/]+)\.tsx$/.exec(path);
  if (core?.[1]) return `core:${core[1]}`;
  const feature = /^\.\.\/features\/([^/]+)\/diagrams\/([^/]+)\.tsx$/.exec(path);
  if (feature?.[1] && feature[2]) return `${feature[1]}:${feature[2]}`;
  return undefined;
}

// One lazy component per diagram, created once at module load (lazy() fetches nothing until rendered).
const components = new Map<string, LazyExoticComponent<ComponentType<DiagramProps>>>();
for (const [path, load] of Object.entries({ ...coreModules, ...featureModules })) {
  const id = idFor(path);
  if (id) components.set(id, lazy(load));
}

/** Whether a diagram with this id exists (used by the concept registry test). */
export function hasDiagram(id: string): boolean {
  return components.has(id);
}

/** Every registered diagram id, sorted. */
export function listDiagramIds(): string[] {
  return [...components.keys()].sort();
}

export function MiniDiagram({ id, className }: { id: string; className?: string }) {
  const Component = components.get(id);
  if (!Component) {
    if (import.meta.env.DEV) console.error(`[MiniDiagram] unknown diagram id "${id}"`);
    return null;
  }
  return (
    <Suspense fallback={<div className={cn("h-24 w-full animate-skeleton rounded-md bg-surface-3", className)} />}>
      {/* The lazy components are created once at module load, so identity is stable across renders. */}
      {/* eslint-disable-next-line react-hooks/static-components */}
      <Component className={className} />
    </Suspense>
  );
}
