/**
 * @synthetic-platform/contracts — the GUI's single import for shared types (server, mock, tests).
 *
 * - `concept`, `concept.schema`: the (i) concept contract (G0a).
 * - `generated/schemas`, `generated/catalogue`, `generated/knobs`, `generated/relationships`,
 *   `generated/dlqRules`: generated from the Python side by `npm run contracts:sync`
 *   (never edited by hand).
 * - `timestamps`: the canonical wire timestamp (six fraction digits, UTC).
 * - `relational`: the relationship-model / DLQ-rule types and `parseEdge` / `formatEdge` / `findEdge`.
 * - `api`: the BFF's request/response contract; `payloads`: profile payload shapes;
 *   `sourceStats`: the profiler entry; `knobs`, `knobs.schema`: the knob file; `vectors`:
 *   the binary chunk envelope.
 *
 * The web app does NOT import this barrel (it would pull classic zod into the shell):
 * it imports types from `@contracts/<module>` and values only from dependency-free
 * modules (`vectors`, `concept`, `relational`, `timestamps`, `generated/catalogue`, `generated/knobs`,
 * `generated/relationships`, `generated/dlqRules`).
 */
export * from "./concept";
export * from "./concept.schema";
export * from "./api";
export * from "./payloads";
export * from "./sourceStats";
export * from "./knobs";
export * from "./knobs.schema";
export * from "./vectors";
export * from "./relational";
export * from "./timestamps";
export * from "../generated/schemas";
export { catalogue, catalogueById, catalogueFamilies, catalogueLevels, catalogueVersion } from "../generated/catalogue";
export type {
  CatalogueFamily,
  CatalogueLevel,
  CatalogueMetric,
  Direction,
  MetricId,
  ScoreFn,
} from "../generated/catalogue";
export { knobs } from "../generated/knobs";
export type { ChannelId, KnobId } from "../generated/knobs";
export { relationships } from "../generated/relationships";
export type { RelationshipModelId } from "../generated/relationships";
export { dlqRules, dlqRuleById } from "../generated/dlqRules";
export type { DlqRuleId } from "../generated/dlqRules";
