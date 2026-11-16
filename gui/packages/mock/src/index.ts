/**
 * @synthetic-platform/mock — the seeded, self-consistent world behind
 * `DATA_SOURCE=mock`: 40 evaluations of an invented thelook-shaped model
 * (storyline.ts), their metric/profile/flag rows computed by packages/stats
 * from generated rows (evaluate.ts), and the generation-side tables — plus one
 * launch whose declared keys the source disproved, with its two evaluations
 * (adjusted.ts, ADR 0038) — and one whole-model launch of 120 unrelated tables
 * in 12 batch jobs, with its evaluation (forest.ts, ADR 0045).
 */
export {
  ADJUSTED_EVALUATIONS,
  ADJUSTED_MODEL,
  ADJUSTED_RELATIONSHIP_MODEL,
  LAUNCH as ADJUSTED_LAUNCH,
} from "./adjusted";
export { catalogueVersionOf, createMockDataset, getMockDataset, SAMPLE_ROWS, type MockDataset } from "./dataset";
export {
  FOREST_BATCHES,
  FOREST_BATCH_SIZE,
  FOREST_EVALUATION,
  FOREST_MODEL,
  FOREST_RELATIONSHIP_MODEL,
  FOREST_TABLES,
  forestRunId,
  type ForestTable,
} from "./forest";
export { baseRunId, launchDigest, referenceDigest, rowsFor, snapshotEra, tableRunIds } from "./ids";
export { chunkRow, EMBEDDING_DIM, MAX_ROW_DOC_ROWS, type ChunkMetaRow, type RagSet } from "./rag";
export { MODEL_URIS, EMBEDDER_URIS, STORYLINE, type EvalSpec } from "./storyline";
export { FREE_TEXT_POOL_MAX } from "./synth";
export {
  EDGES,
  edgeLabel,
  edgeRole,
  landingFqn,
  MOCK_RELATIONSHIP_MODEL,
  RELATIONSHIP_MODEL,
  sourceFqn,
  TABLES,
} from "./thelook";
