/**
 * The relationship model and the DLQ rule map, as `scripts/gui/export_knobs.py`
 * writes them (`generated/relationships.json`, `generated/dlq_rules.json`; typed
 * copies in `generated/relationships.ts` and `generated/dlqRules.ts`), plus the
 * edge-label helpers every tab uses to join an `edge` column back to the model.
 *
 * Dependency-free (no zod): the web shell may import it.
 */

/** The commit the `path:line` links of an export resolve at; `dirty` lists referenced files with uncommitted edits. */
export interface ExportedFrom {
  commit: string | null;
  dirty: string[];
}

/**
 * How a launch treats an FK edge (sdfb_core RelationshipRegistry.edge_roles, ADR 0036/0037):
 * `driving` fans the child out of its parent's keys, `implied` is satisfied by a driving
 * edge, `conditional` draws a key consistent with the driving parent, `independent` draws
 * from the parent's key pool, `external` draws from a table the launch does not generate,
 * `documented` is declared with `enforced: false` (no launch behavior; the evaluator
 * reports its orphan rate as INFO), `disabled` belongs to a table with `enabled: false`.
 */
export type EdgeRole = "driving" | "implied" | "conditional" | "independent" | "external" | "documented" | "disabled";

export interface RelationEdge {
  cols: string[];
  /** The parent table: a bare name in the model, or `dataset.table` for an external parent. */
  ref: string;
  ref_cols: string[];
  enforced: boolean;
  drives: boolean;
  external: boolean;
  role: EdgeRole;
  /** The columns the launch actually draws (a driving edge may be widened, ADR 0036). */
  drawn_cols: string[];
  note: string;
}

export interface RelationTable {
  name: string;
  pk: string[];
  identity: string[];
  enabled: boolean;
  note: string;
  fk: RelationEdge[];
}

export interface RelationshipModel {
  model: string;
  description: string;
  /** Repo-relative path of the YAML model. */
  source: string;
  /** RelationshipRegistry.sha12() — what the launcher logs as the model fingerprint. */
  sha12: string;
  /** Parents before children, component by component. */
  generation_order: string[];
  tables: RelationTable[];
}

export interface RelationshipsFile {
  generated_by: string;
  note: string;
  models: RelationshipModel[];
  exported_from: ExportedFrom;
}

/** One DLQ rule: what the code emits and what config/thresholds.yml declares. */
export interface DlqRule {
  rule_id: string;
  /** A DoFn builds a DLQ envelope with this rule_id. */
  emitted: boolean;
  /** The envelope's error_type (`pydantic`, `pandera`, `uniqueness`, `referential_integrity`, `engine`, `load_safety`). */
  error_type: string | null;
  /** The step `dlq.normalize_dlq_record` assigns; null when it has no mapping. */
  pipeline_step: string | null;
  /** `pre_generate` | `pre_write` */
  stage: string | null;
  /** "path:line" of the envelope. */
  emitted_by: string | null;
  declared: boolean;
  /** BLOCKER | CRITICAL | MAJOR | MINOR | INFO, from thresholds.yml. */
  severity: string | null;
  dimension: string | null;
  /** `in_dag` (the DAG can DLQ it) | `post_run` (scored offline, never DLQ'd). */
  scope: string | null;
  /** In sdfb_core.validation.summary.BLOCKER_RULE_IDS (counts toward FAILED_BLOCKER). */
  counted_in_blocker_gate: boolean;
}

export interface DlqRulesFile {
  generated_by: string;
  note: string;
  rules: DlqRule[];
  exported_from: ExportedFrom;
}

/**
 * A parsed edge label. The evaluator's `edge` column reads `child.col->parent.col`
 * (composite keys join columns with `+`; an external parent keeps its dataset:
 * `order_items.product_id->synthetic_data.products.id`). The launcher logs
 * `(col,col)->parent`, which has no child and no parent columns.
 */
export interface EdgeRef {
  child: string | null;
  cols: string[];
  parent: string;
  parentCols: string[] | null;
}

const NAME = /^[A-Za-z_][\w$-]*$/;

function splitQualified(side: string): { table: string; cols: string[] } | null {
  const dot = side.lastIndexOf(".");
  if (dot <= 0 || dot === side.length - 1) return null;
  const table = side.slice(0, dot);
  const cols = side.slice(dot + 1).split("+");
  if (!table.split(".").every((part) => NAME.test(part)) || !cols.every((c) => NAME.test(c))) return null;
  return { table, cols };
}

/** Parses either label form; null when the text is neither. */
export function parseEdge(label: string): EdgeRef | null {
  const arrow = label.indexOf("->");
  if (arrow < 0 || label.indexOf("->", arrow + 2) >= 0) return null;
  const left = label.slice(0, arrow).trim();
  const right = label.slice(arrow + 2).trim();
  const launcher = /^\(([^()]+)\)$/.exec(left);
  if (launcher) {
    const cols = (launcher[1] ?? "").split(",").map((c) => c.trim());
    if (!cols.every((c) => NAME.test(c)) || !right.split(".").every((part) => NAME.test(part))) return null;
    return { child: null, cols, parent: right, parentCols: null };
  }
  const child = splitQualified(left);
  const parent = splitQualified(right);
  if (!child || !parent || child.cols.length !== parent.cols.length) return null;
  return { child: child.table, cols: child.cols, parent: parent.table, parentCols: parent.cols };
}

/** The evaluator's label for an edge of `child`. */
export function formatEdge(child: string, edge: Pick<RelationEdge, "cols" | "ref" | "ref_cols">): string {
  return `${child}.${edge.cols.join("+")}->${edge.ref}.${edge.ref_cols.join("+")}`;
}

/** The model edge a label names (either form), with its child table; null when the model has none. */
export function findEdge(model: RelationshipModel, label: string): { table: RelationTable; edge: RelationEdge } | null {
  const ref = parseEdge(label);
  if (!ref) return null;
  const same = (a: string[], b: string[]) => a.length === b.length && a.every((v, i) => v === b[i]);
  for (const table of model.tables) {
    if (ref.child !== null && table.name !== ref.child) continue;
    for (const edge of table.fk) {
      if (edge.ref !== ref.parent) continue;
      const declared = same(edge.cols, ref.cols) && (ref.parentCols === null || same(edge.ref_cols, ref.parentCols));
      // A widened driving edge is labelled by the columns the launch drew.
      if (declared || same(edge.drawn_cols, ref.cols)) return { table, edge };
    }
  }
  return null;
}
