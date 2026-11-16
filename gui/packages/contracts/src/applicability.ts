/**
 * Key checks with nothing to check. When the relationship model declares no primary key (or no
 * identity columns) for a table, the evaluator writes `table.pk_duplicate_rate` (or
 * `table.identity_duplicate_rate`) as `not_evaluated` with one of the two reasons below —
 * `_NO_PK` and `_NO_IDENTITY` of `sdfb_evaluation/beam/membership.py`, copied verbatim
 * (`applicability.test.ts` reads that file). The GUI shows such a row as "Not applicable": not a
 * gap in the evaluation, and never a FAIL. Every other not-evaluated row stays "Not evaluated".
 *
 * Dependency-free (no zod): the web shell may import it.
 */
export const NO_PK_REASON = "no primary key declared in the relationship model";
export const NO_IDENTITY_REASON = "no identity columns declared in the relationship model";

export type InapplicableKey = "pk" | "identity";

const BY_METRIC: Readonly<Record<string, { key: InapplicableKey; reason: string }>> = {
  "table.pk_duplicate_rate": { key: "pk", reason: NO_PK_REASON },
  "table.identity_duplicate_rate": { key: "identity", reason: NO_IDENTITY_REASON },
};

/** "pk" / "identity" when the row is a key check the model gave nothing to check; null otherwise. */
export function inapplicableKey(row: { metric_id: string; status: string; detail?: unknown }): InapplicableKey | null {
  const entry = BY_METRIC[row.metric_id];
  if (!entry || row.status !== "not_evaluated") return null;
  const detail = row.detail;
  const reason =
    detail && typeof detail === "object" && !Array.isArray(detail)
      ? (detail as { reason?: unknown }).reason
      : undefined;
  return reason === entry.reason ? entry.key : null;
}

/** The grey label the views show. */
export const NOT_APPLICABLE_LABEL: Readonly<Record<InapplicableKey, string>> = {
  pk: "Not applicable — no primary key declared",
  identity: "Not applicable — no identity columns declared",
};
