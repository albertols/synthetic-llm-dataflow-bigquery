/**
 * Parameters: what the generation run was launched with and how the
 * evaluator ran — the typed registry columns first (what the list filters
 * on), then the two JSON snapshots verbatim.
 */
import type { EvaluationRecord } from "@contracts/api";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatCount, formatDateTime, MISSING } from "@/lib/format";

import { JsonView } from "../components/JsonView";
import { fmtSig } from "../lib/format";

export function ParamsPanel({ evaluation }: { evaluation: EvaluationRecord }) {
  const typed: [string, string][] = [
    ["engine", evaluation.engine ?? MISSING],
    ["llm_model_uri", evaluation.llm_model_uri ?? MISSING],
    ["embedder_id", evaluation.embedder_id ?? MISSING],
    ["retrieval_method", evaluation.retrieval_method ?? MISSING],
    ["similarity", fmtSig(evaluation.similarity)],
    ["seed", evaluation.seed ?? MISSING],
    ["reference_rows_limit", formatCount(evaluation.reference_rows_limit)],
    ["num_rows_requested", formatCount(evaluation.num_rows_requested)],
    ["source_stats_tier", evaluation.source_stats_tier ?? MISSING],
    ["profiler_version", evaluation.profiler_version ?? MISSING],
    ["uniqueness_mode", evaluation.uniqueness_mode ?? MISSING],
    ["freetext_expansion", evaluation.freetext_expansion ?? MISSING],
    ["write_disposition", evaluation.write_disposition ?? MISSING],
    ["client_type · vllm_dtype", `${evaluation.client_type ?? MISSING} · ${evaluation.vllm_dtype ?? MISSING}`],
    [
      "relationship_model",
      `${evaluation.relationship_model ?? MISSING} (${evaluation.relationship_model_sha ?? "no sha"})`,
    ],
    ["model_adjusted", evaluation.model_adjusted === null ? MISSING : String(evaluation.model_adjusted)],
    ["generation_job", `${evaluation.generation_job_name ?? MISSING} · ${evaluation.generation_job_id ?? MISSING}`],
    [
      "generation window",
      `${formatDateTime(evaluation.generation_started_at)} → ${formatDateTime(evaluation.generation_finished_at)}`,
    ],
    ["evaluation_job_id", evaluation.evaluation_job_id ?? MISSING],
    ["image", evaluation.image ?? MISSING],
    ["artifacts_uri", evaluation.artifacts_uri ?? MISSING],
    ["evaluation_key", evaluation.evaluation_key],
  ];
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle>Registry columns</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid">
            {typed.map(([k, v]) => (
              <div
                key={k}
                className="grid grid-cols-[minmax(7rem,11rem)_minmax(0,1fr)] gap-3 border-b border-border py-1 font-mono text-xs last:border-0"
              >
                <span className="text-text-2">{k}</span>
                <span className="break-all text-text-1">{v}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>generation_params</CardTitle>
        </CardHeader>
        <CardContent>
          <JsonView value={evaluation.generation_params} label="generation_params" />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>evaluation_params</CardTitle>
        </CardHeader>
        <CardContent>
          <JsonView value={evaluation.evaluation_params} label="evaluation_params" />
        </CardContent>
      </Card>
    </div>
  );
}
