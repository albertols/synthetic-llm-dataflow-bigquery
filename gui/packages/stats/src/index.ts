/**
 * @synthetic-platform/stats — pure TypeScript maths for the GUI and the mock.
 *
 * - intervals: dkwEpsilon, ksCritical, wilson, newcombe, rateRatio, aucInterval, noiseFloor …
 * - distances / entropy: tvd, jsdBits, psi, cohensW, hellinger, entropyBits, normalizedEntropy
 * - distributions: ecdf, quantiles, histogram, ksBracket, w1FromBins, pitW1, quantilesFromBins
 * - correlation: pearson, spearman, cramersV, nmi, contingencyTvd
 * - linalg: cosine, pca3, knnPreservation
 * - exact ports (goldens): hashingEmbed (HashingEmbedder), serializeGreat (GReaT),
 *   centroidTopK / kcenter / kcenterRotate / selectSeedExamples (retrieval)
 * - teaching only (never in the pipeline): mmr, randomPick
 * - scale: rarefaction, birthdayCollisionProb, rareCaptureProb, tailPoints, poolReuse
 * - scoring: scoreValue, statusFor, tableFamilyScores, modelFamilyScores
 * - rng: mulberry32, Random, seedFrom
 */
export * from "./correlation";
export * from "./distances";
export * from "./distributions";
export * from "./entropy";
export * from "./great";
export * from "./hashing";
export * from "./intervals";
export * from "./linalg";
export * from "./retrieval";
export * from "./rng";
export * from "./scale";
export * from "./scoring";
export * from "./sha256";
export * from "./special";
export * from "./teaching";
