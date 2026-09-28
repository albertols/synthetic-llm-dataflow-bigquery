/**
 * @synthetic-platform/contracts — the GUI's single import for shared types (server, mock, tests).
 *
 * - `concept`, `concept.schema`: the (i) concept contract (G0a).
 * - `generated/schemas`, `generated/catalogue`, `generated/knobs`: generated from the
 *   Python side by `npm run contracts:sync` (never edited by hand).
 * - `api`: the BFF's request/response contract; `payloads`: profile payload shapes;
 *   `sourceStats`: the profiler entry; `knobs`, `knobs.schema`: the knob file; `vectors`:
 *   the binary chunk envelope.
 *
 * The web app does NOT import this barrel (it would pull classic zod into the shell):
 * it imports types from `@contracts/<module>` and values only from dependency-free
 * modules (`vectors`, `concept`, `generated/catalogue`, `generated/knobs`).
 */
export * from "./concept";
export * from "./concept.schema";
export * from "./api";
export * from "./payloads";
export * from "./sourceStats";
export * from "./knobs";
export * from "./knobs.schema";
export * from "./vectors";
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
