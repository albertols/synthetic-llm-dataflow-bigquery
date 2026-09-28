/**
 * @synthetic-platform/mock — the seeded, self-consistent world behind
 * `DATA_SOURCE=mock`: 40 evaluations of an invented thelook-shaped model
 * (storyline.ts), their metric/profile/flag rows computed by packages/stats
 * from generated rows (evaluate.ts), and the generation-side tables.
 */
export { catalogueVersionOf, createMockDataset, getMockDataset, SAMPLE_ROWS, type MockDataset } from "./dataset";
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
