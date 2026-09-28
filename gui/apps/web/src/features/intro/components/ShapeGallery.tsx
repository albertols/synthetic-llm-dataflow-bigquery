/**
 * The relationship-shapes gallery. Shapes held by a committed sample model
 * are drawn from `/api/relationships` (the committed models, plus the mock's
 * `thelook_demo` in mock mode), falling back to the models exported into the
 * contracts while the request is in flight or failed. The graph card is the
 * thelook example, highlighted; in mock mode it can switch to the mock's
 * variant, whose users edge is documented rather than implied.
 */
import { useMemo, useState } from "react";

import type { RelationshipModel } from "@contracts/relational";
import { relationships as committed } from "@contracts/generated/relationships";

import { InfoHint } from "@/components/InfoHint";
import { Badge } from "@/components/ui/badge";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useRelationships } from "@/lib/api";
import { cn } from "@/lib/cn";
import { repoBlobUrl } from "@/lib/links";

import type { Graph } from "../content/graphLayout";
import { graphFromModel } from "../content/graphLayout";
import { ROLE_ORDER, ROLE_STYLE, SHAPES, SHAPES_LEAD, shapeGraph, type ShapeSpec } from "../content/shapes";
import { ExternalAnchor, InlineCode, IntroSection } from "./primitives";
import { RelationGraph } from "./RelationGraph";

type ModelWithOrigin = RelationshipModel & { origin: "committed_example" | "mock" };

const THELOOK_MODELS = ["gcp_public_thelook", "thelook_demo"] as const;

export function ShapeGallery() {
  const query = useRelationships();
  const models: ModelWithOrigin[] = useMemo(
    () =>
      query.data?.data.models ?? committed.models.map((model) => ({ ...model, origin: "committed_example" as const })),
    [query.data],
  );
  return (
    <IntroSection
      id="shapes"
      eyebrow="Relational generation"
      title="Relationship shapes one model can declare"
      concept="intro:relationship-model"
      lead={
        <>
          “{SHAPES_LEAD}” —{" "}
          <ExternalAnchor href={`${repoBlobUrl("docs/DESIGN.md")}#42-several-parents-every-edge-gets-a-role`}>
            DESIGN.md §4.2
          </ExternalAnchor>
          . Arrows point from a child to the parent it references.
        </>
      }
    >
      <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3" aria-label="Relationship shapes">
        {SHAPES.map((spec) =>
          spec.id === "graph" ? (
            <li key={spec.id} className="min-w-0 md:col-span-2">
              <ThelookCard spec={spec} models={models} />
            </li>
          ) : (
            <li key={spec.id} className="min-w-0">
              <ShapeCard spec={spec} graph={shapeGraph(spec, models)} />
            </li>
          ),
        )}
        <li className="min-w-0 xl:col-span-2">
          <RoleLegend />
        </li>
      </ul>
    </IntroSection>
  );
}

function ShapeCard({ spec, graph }: { spec: ShapeSpec; graph: Graph | null }) {
  const headingId = `shape-${spec.id}`;
  return (
    <article
      aria-labelledby={headingId}
      data-shape={spec.id}
      className="flex h-full flex-col gap-3 rounded-lg border border-border bg-surface-1 p-4"
    >
      <div className="flex items-center gap-1">
        <h3 id={headingId} className="text-base font-semibold text-text-1">
          {spec.title}
        </h3>
        <InfoHint concept={spec.concept} />
      </div>
      {graph ? (
        <RelationGraph graph={graph} title={`${spec.title} shape`} />
      ) : (
        <p className="text-sm text-text-3">This shape&apos;s sample model is not in the contracts.</p>
      )}
      <p className="text-sm leading-snug text-text-2">
        <InlineCode text={spec.caption} />
      </p>
      <ShapeFooter spec={spec} graph={graph} />
    </article>
  );
}

function ShapeFooter({ spec, graph }: { spec: ShapeSpec; graph: Graph | null }) {
  return (
    <div className="mt-auto grid gap-2 border-t border-border pt-3 text-xs text-text-3">
      <p>
        {spec.source.kind === "model" ? (
          <ModelSource name={spec.source.model} />
        ) : (
          <>
            From the README&apos;s shape sweep, pinned by{" "}
            <ExternalAnchor href={repoBlobUrl("packages/sdfb-tests/tests/unit/contracts/test_relationship_shapes.py")}>
              test_relationship_shapes.py
            </ExternalAnchor>
          </>
        )}
      </p>
      {graph ? <EdgeList graph={graph} /> : null}
    </div>
  );
}

function ModelSource({ name }: { name: string }) {
  const model = committed.models.find((m) => m.model === name);
  return model ? (
    <>
      From <ExternalAnchor href={repoBlobUrl(model.source)}>{model.source.split("/").pop()}</ExternalAnchor>
    </>
  ) : (
    <>From the {name} model</>
  );
}

function EdgeList({ graph }: { graph: Graph }) {
  return (
    <details className="group">
      <summary className="cursor-pointer text-text-2 hover:text-text-1">Edges ({graph.edges.length}) as text</summary>
      <ul className="mt-2 grid gap-1">
        {graph.edges.map((edge) => (
          <li key={`${edge.from}->${edge.to}:${edge.cols.join(",")}`} className="font-mono text-[11px] text-text-2">
            {edge.from}.({edge.cols.join(", ")}) → {edge.to}.({edge.refCols.join(", ")}){" "}
            <span className="text-text-1">· {edge.role}</span>
            {edge.oneToOne ? " · 1:1" : ""}
            {edge.note ? <span className="block pl-3 font-sans text-text-3">{edge.note}</span> : null}
          </li>
        ))}
      </ul>
    </details>
  );
}

function ThelookCard({ spec, models }: { spec: ShapeSpec; models: readonly ModelWithOrigin[] }) {
  const available = THELOOK_MODELS.map((name) => models.find((m) => m.model === name)).filter(
    (m): m is ModelWithOrigin => m !== undefined,
  );
  const [choice, setChoice] = useState<string>(THELOOK_MODELS[0]);
  const model = available.find((m) => m.model === choice) ?? available[0];
  const tables = spec.source.kind === "model" ? spec.source.tables : [];
  const graph = model ? graphFromModel(model, tables) : null;
  const headingId = `shape-${spec.id}`;
  return (
    <article
      aria-labelledby={headingId}
      data-shape={spec.id}
      className="relative flex h-full flex-col gap-3 rounded-lg border border-accent/70 bg-surface-1 p-4 shadow-[0_0_0_1px_var(--accent-soft),0_12px_32px_-18px_var(--accent)] md:p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 id={headingId} className="text-base font-semibold text-text-1">
            {spec.title}: the thelook example
          </h3>
          <InfoHint concept={spec.concept} />
          <Badge variant="accent">highlighted</Badge>
        </div>
        {available.length > 1 ? (
          <ToggleGroup
            type="single"
            value={model?.model}
            onValueChange={(value) => value && setChoice(value)}
            aria-label="thelook model"
          >
            {available.map((m) => (
              <ToggleGroupItem key={m.model} value={m.model}>
                {m.origin === "mock" ? "Mock model" : "Committed example"}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        ) : null}
      </div>
      <div className="grid gap-4 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] md:items-center">
        {graph ? (
          <RelationGraph graph={graph} title={`thelook: ${model?.description ?? ""}`} highlight />
        ) : (
          <p className="text-sm text-text-3">No thelook model is available.</p>
        )}
        <div className="grid content-start gap-3 text-sm">
          <p className="leading-snug text-text-2">
            <InlineCode text={spec.caption} />
          </p>
          {model ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              <dt className="text-text-3">Model</dt>
              <dd className="font-mono text-text-1">{model.model}</dd>
              <dt className="text-text-3">Declared in</dt>
              <dd className="min-w-0 [overflow-wrap:anywhere]">
                {model.origin === "mock" ? (
                  <span className="font-mono text-text-2">{model.source}</span>
                ) : (
                  <ExternalAnchor href={repoBlobUrl(model.source)} className="font-mono">
                    {model.source}
                  </ExternalAnchor>
                )}
              </dd>
              <dt className="text-text-3">Generated in order</dt>
              <dd className="font-mono text-text-2">{model.generation_order.join(" → ")}</dd>
              <dt className="text-text-3">Fingerprint</dt>
              <dd className="font-mono text-text-2">{model.sha12}</dd>
            </dl>
          ) : null}
          {model?.origin === "mock" ? (
            <p className="rounded-md border border-border bg-surface-2 p-2 text-xs text-text-2">
              The mock variant declares order_items → users with <code className="font-mono">enforced: false</code>: a
              documented edge, whose orphan rate the evaluator reports as INFO.
            </p>
          ) : null}
        </div>
      </div>
      {graph ? (
        <div className="border-t border-border pt-3 text-xs text-text-3">
          <EdgeList graph={graph} />
        </div>
      ) : null}
    </article>
  );
}

function RoleLegend() {
  return (
    <article
      aria-labelledby="shape-roles"
      className="flex h-full flex-col gap-3 rounded-lg border border-border bg-surface-1 p-4"
    >
      <h3 id="shape-roles" className="text-base font-semibold text-text-1">
        Every edge gets a role
      </h3>
      <p className="text-sm text-text-2">
        Roles are derived from the declared columns and the model&apos;s own graph; nothing new is declared.
      </p>
      <ul className="grid gap-x-4 gap-y-2 sm:grid-cols-2">
        {ROLE_ORDER.map((role) => {
          const style = ROLE_STYLE[role];
          return (
            <li key={role} className="flex items-center gap-2 text-sm">
              <svg aria-hidden="true" width="34" height="10" className="shrink-0">
                <line
                  x1="1"
                  y1="5"
                  x2="33"
                  y2="5"
                  stroke={style.stroke}
                  strokeWidth={style.width + 0.4}
                  strokeDasharray={style.dash}
                  strokeLinecap="round"
                />
              </svg>
              <span className={cn("font-mono text-xs", role === "driving" ? "text-accent-text" : "text-text-1")}>
                {role}
              </span>
              <InfoHint concept={`intro:role-${role}`} />
            </li>
          );
        })}
      </ul>
    </article>
  );
}
