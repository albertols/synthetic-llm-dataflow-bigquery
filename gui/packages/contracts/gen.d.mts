/** Types for gen.mjs (the contracts generator), for its TypeScript test. */
export function generate(options: { repo: string; inputsDir: string }): Map<string, string>;
export function diffGenerated(files: Map<string, string>, outDir: string): string[];
export function main(argv?: string[]): number;
