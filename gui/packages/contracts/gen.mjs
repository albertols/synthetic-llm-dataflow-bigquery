// contracts:sync / contracts:check.
//
// Task G0b replaces this stub with the generator: it reads the Python-side
// JSON schemas, the metric catalogue, knobs.json and the golden fixtures, and
// writes generated/** (zod). `--check` regenerates into a temp directory and
// fails on drift. Until then there is nothing generated, so nothing can drift.
const check = process.argv.includes("--check");
console.log(
  `contracts:${check ? "check" : "sync"}: no generated contracts yet (the generator lands in task G0b); nothing to ${check ? "compare" : "write"}.`,
);
