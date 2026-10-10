/**
 * The one filter row (URL state): table → reference sample → embedder →
 * space → seed strategy. Everything below re-renders against it.
 */
import { InfoHint } from "@/components/InfoHint";
import { formatCount, formatDate } from "@/lib/format";

import { FieldSelect } from "./FieldSelect";
import { listTables, spaceLabel, tablesPeers, type SpaceId } from "./lib/selection";
import { STRATEGIES, STRATEGY_IDS, type StrategyId } from "./lib/strategies";
import type { RagModel } from "./useRagModel";

export function RagFilters({ model }: { model: RagModel }) {
  const { resolved, sets, setSearch, strategy } = model;
  if (!resolved) return null;
  const { set, samples, table } = resolved;
  const spaces: SpaceId[] = [
    "rows",
    ...set.columns.map((c) => `values:${c}` as SpaceId),
    ...(set.value_chunks > 0 ? (["all"] as SpaceId[]) : []),
    ...(tablesPeers(set, sets).length > 1 ? (["tables"] as SpaceId[]) : []),
  ];
  const sample = samples.find((s) => s.digest === set.reference_digest);
  const spaceCount = (space: SpaceId): string => {
    if (space === "rows") return `${formatCount(set.row_docs)} vectors`;
    if (space === "all") return `≤ 3,000 of ${formatCount(set.row_docs + set.value_chunks)}`;
    if (space === "tables") return `${tablesPeers(set, sets).length} tables`;
    return "value chunks";
  };
  return (
    <div
      role="group"
      aria-label="What to show"
      className="grid grid-cols-1 gap-3 rounded-lg border border-border bg-surface-1 p-3 sm:grid-cols-2 lg:grid-cols-5"
    >
      <FieldSelect
        label="Table"
        value={table}
        options={listTables(sets).map((t) => ({ value: t, label: t }))}
        onChange={(value) => setSearch({ table: value, digest: undefined, space: undefined, model: undefined })}
      />
      <FieldSelect
        label="Reference sample"
        hint={<InfoHint concept="rag:reference-digest" />}
        value={set.reference_digest}
        options={samples.map((s) => ({
          value: s.digest,
          label: s.digest.slice(0, 10),
          hint: s.createdAt ? formatDate(s.createdAt) : undefined,
        }))}
        onChange={(value) => setSearch({ digest: value, space: undefined, model: undefined })}
      />
      <FieldSelect
        label="Embedder"
        hint={<InfoHint concept={set.embedder_id === "hashing-384" ? "rag:hashing-embedder" : "rag:bge"} />}
        value={set.embedder_id}
        options={(sample?.embedders ?? [set.embedder_id]).map((e) => ({
          value: e,
          label: e,
          hint: e === "hashing-384" ? "lexical" : "semantic",
        }))}
        onChange={(value) => setSearch({ embedder: value })}
      />
      <FieldSelect
        label="Space"
        hint={<InfoHint concept="rag:chunk-kinds" />}
        value={resolved.space}
        options={spaces.map((s) => ({ value: s, label: spaceLabel(s), hint: spaceCount(s) }))}
        onChange={(value) => setSearch({ space: value })}
      />
      <FieldSelect
        label="Seed strategy"
        hint={<InfoHint concept={STRATEGIES[strategy].concept} />}
        value={strategy}
        options={STRATEGY_IDS.map((id) => ({
          value: id,
          label: STRATEGIES[id].label,
          hint: STRATEGIES[id].inPipeline ? "pipeline" : "teaching only",
        }))}
        onChange={(value) => setSearch({ strategy: value as StrategyId })}
      />
    </div>
  );
}
