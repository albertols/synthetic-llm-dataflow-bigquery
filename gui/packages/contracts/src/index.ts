/**
 * @synthetic-platform/contracts — the GUI's single import for shared types.
 *
 * Today it carries the concept contract. Task G0b adds the zod types generated
 * from the Python side (`generated/**`, written by `npm run contracts:sync`).
 */
export * from "./concept";
export * from "./concept.schema";
