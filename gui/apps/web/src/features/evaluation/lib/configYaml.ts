/**
 * "Download as config.yaml" (ADR 0044). Its own module so the `yaml` library reaches only the
 * Generator panel, never the evaluation list (which imports lib/record.ts).
 */
import type { GenerationRecord } from "@contracts/record";
import { stringify } from "yaml";

/**
 * The snapshot's `values` under a comment naming the run and the version. Written as YAML 1.1 so
 * the generator's PyYAML reads every string as a string (`off`, `"8192"`). Null without a snapshot.
 */
export function configYaml(record: GenerationRecord): string | null {
  const values = record.snapshot?.values;
  if (!values) return null;
  const run = record.runs.find((r) => r.run_id === record.snapshot_run_id);
  const version = run?.generator_version ?? record.launcher_version ?? "unknown";
  const job = run?.job_id ? ` \u00b7 Dataflow job ${run.job_id}` : "";
  const header = [
    `# Generator config of run ${record.snapshot_run_id}${job} \u00b7 generator ${version}.`,
    "# Rendered by the GUI from validation_runs.config (ADR 0044). Every resolved value is pinned except",
    "# derived defaults (null) and the run id, which stays a flag. Pass the file with --config_uri to",
    "# replay the launch; flags then change nothing it sets (ADR 0043).",
  ];
  const configVersion = typeof record.snapshot?.config_version === "number" ? record.snapshot.config_version : 1;
  return `${header.join("\n")}\n${stringify({ config_version: configVersion, ...values }, { version: "1.1" })}`;
}
