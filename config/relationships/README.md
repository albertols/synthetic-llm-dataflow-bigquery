# `config/relationships/` — the relational source of truth

One YAML file per relational model. This folder decides which tables have
PKs, which reference which, and which of those relationships a run
actually generates from. Nothing else does: table descriptions in
BigQuery are **not** read for relational structure (ADR 0032).

```bash
# it is already the default — this is what a launch does implicitly
--relationships_uri=config/relationships

# a GCS FOLDER: every *.yaml / *.yml directly under that prefix
--relationships_uri=gs://my-bucket/relationships

# or one specific file
--relationships_uri=gs://my-bucket/relationships/corp_model.yaml

# relationships off on purpose (every table generates alone)
--relationships_uri=""
```

Three things to know about a `gs://` override:

- **One level, no recursion.** The match is `<uri>/*.yaml` and
  `<uri>/*.yml`; Beam's `*` does not cross `/`, so
  `gs://bucket/models/legacy/x.yaml` is NOT picked up by
  `gs://bucket/models`. Trailing slash optional.
- **The extension matters.** A file without `.yaml`/`.yml` is invisible.
- **A wrong URI stops the launch.** An unlistable path, or a path you
  passed explicitly that holds no models, raises — it never degrades into
  "no relationships declared", which would generate every table alone and
  still look like a successful run. Only the packaged default may be
  empty. The launcher's service account needs
  `storage.objects.list` + `get` on the bucket.

## The whole schema

```yaml
model: retail                 # required: the model's id, shown in the log card
description: orders chain     # optional prose, shown in the card

tables:
  A_TABLE:                    # key = table NAME (no dataset, no project)
    pk:       [COL_A, COL_B]  # composite is fine; [] or omitted = no PK
    identity: [COL_C]         # unique-but-not-key columns
    enabled:  true            # default true — see "Detaching" below
    note:     account master  # optional, for humans
    fk:
      - cols:     [COL_D, COL_E]      # this table's columns
        ref:      B_TABLE             # a table in THIS model…
        ref_cols: [COL_F, COL_G]      # …and its columns, same arity
        enforced: true                # default true — see "Three flags"
        drives:   false               # default false — see "Three flags"
        note:     optional prose
```

`ref` names a table in the same model (bare name) **or** an already-landed
parent outside it (`dataset.table` — dataset-qualified is what marks it
external). `ref_cols` need not be the parent's full PK: any projection
works, because the child draws from `SELECT DISTINCT ref_cols` of the
parent's landed rows.

## Samples are never loaded from a directory scan

`example_*.yaml` and `*.example.yaml` are documentation. The loader skips
them when it scans a directory (`relationships_example_skipped` in the
launcher log), so a real model that reuses the sample's anonymised aliases
(`A_TABLE`, `B_TABLE`, …) never collides with it. To load a sample on
purpose, point `--relationships_uri` at the file itself.

## Toggling tables: what the registry derives for you

`enabled: true/false` is the only edit a launch needs. When a child ends up
with several enforced edges and none is marked `drives: true`, the registry
picks the **most-derived parent** — the candidate that itself descends from
every other candidate over enforced edges (A_TABLE → C_TABLE drives when
C_TABLE → B_TABLE exists). It then **widens** the driving parent's edge to
the other parent with the column pairs the child pins by referencing the
same columns in both (`fk_edge_widened` in the launcher log), so the other
edge is *implied* and the inherited columns are copied from the
grandparent. When no parent descends from the others, the
FIRST DECLARED enforced edge drives and the launcher says so
(`fk_driving_edge_defaulted`, WARNING) — `drives: true` is how you
choose a different one. See "Which edge drives" below.

## Three flags, three different jobs

| Flag | Where | Meaning |
|---|---|---|
| `enabled: false` | table | **Detach.** The table leaves the graph; anything that reached the rest of the model only through it detaches with it. It still generates when you target it directly — the flag governs participation, not permission. A child's edge to a disabled parent is not drawn and not counted by preflight P4: that FK member keeps its own route (pattern, typed, categorical). |
| `enforced: false` | edge | **Document only.** The relationship is real and appears in the card and the diagram, but no keys are drawn from it and `fk.orphan` has nothing to check. For join keys that exist in the business model and not in the DDL. |
| `drives: true` | edge | **Driving edge.** When a child has multiple enforced in-model parents, mark exactly one edge `drives: true` — the parent whose keys this table is generated from (ADR 0036). Every other enforced edge must be **implied** by that parent's relational structure, else the launch stops. |

Worked example: `A ← B ← C`. Set `enabled: false` on `B` and a launch on
`A` generates `A` alone — `C` reached `A` only through `B`.

### Driven fan-out: widened edges and the implied rule (ADR 0036)

A child whose PK contains its parent's FK (a per-parent key, not a
table-wide one) is generated FROM its parent's landed keys instead of a
random draw against a sampled key pool — see ADR 0036 for why a random
draw collides. That child needs exactly one **driving edge**: the
parent whose keys it consumes. Everything else follows from `cols` and
`drives`:

- **Widen the driving edge to carry inherited columns.** `ref_cols`
  need not be the parent's PK alone — add every column a grandchild
  will need FROM this table's own key tuple. `C_TABLE`'s edge to
  `B_TABLE` widens from `[D_COL_001] → B_TABLE` to `[D_COL_001,
  D_COL_024, D_COL_025, C_COL_009] → B_TABLE (same cols)`: the tuple
  already travels jointly, so grouping by the wide tuple equals
  grouping by the account, and `A_TABLE` (driven by `C_TABLE`) copies
  the extra columns straight from the key instead of sampling them.
- **One driving edge, the rest implied.** When a child has several
  enforced in-model edges, mark exactly one `drives: true`. An edge is
  **implied** — satisfied by construction, no keys drawn from it, no
  launch stop — when its columns are a subset of the driving edge's
  columns AND the driving parent carries those columns from that other
  parent through its own enforced edge, transitively. `A_TABLE`'s edge
  straight to `B_TABLE` is implied through `C_TABLE`'s widened edge
  above: `A_TABLE` never draws `B_TABLE` keys itself, but its rows are
  still valid children of `B_TABLE` because `C_TABLE` already proved
  it. An edge that is neither driving nor implied is **not** a stop
  any more: it is `independent` or `conditional` (ADR 0037 — next
  section).

### Multi-parent children: every edge has a role (ADR 0037)

A child may reference several parents. The registry gives **every**
enforced edge one of five roles — you never declare a role, and since
ADR 0037 no combination of edges is a launch stop except two `drives:
true` markers on one table.

| role | when | DAG path | what a row gets |
|---|---|---|---|
| `driving` | the parent whose landed keys this child is generated from (ADR 0036) | the key-batch request stream | the whole driving tuple + inherited columns + PK cells |
| `implied` | its columns are a subset of the driving edge's AND the driving parent carries them from that parent, transitively | none — nothing to draw | satisfied through the driving edge |
| `independent` | shares **no** column with the driving edge (a star-schema dimension) | the sampled key pool as a side input (ADR 0030/0031) | a whole parent key tuple, drawn per row |
| `conditional` | shares **at least one** column with the driving edge (a diamond branch) | a co-partitioned `CoGroupByKey` on the shared columns, before batching | shared columns from the driving key; the rest is a candidate that exists in the parent for that shared value |
| `external` | the parent is outside the launch (`dataset.table`) | the driver-side key pool | a whole tuple |

Overlap is by **child column name**: the child column `T` in `(T,L) ->
left` and in `(T,R) -> right` is ONE column with ONE value, so it must
satisfy both parents — that is why an overlapping edge cannot ride a
side-input pool (the pool would overwrite `T`) and gets the join
instead. The edge's columns outside the overlap are its `rest`; a
`rest` that is empty makes the edge a pure **existence filter** (the
child's key must also exist in that parent).

The card names the role on every edge, so `card.py` is the check:

```text
        |   +- (B_KEY) --> dim_b (B_KEY)     [enforced, independent]
        |   +- (T,R) --> right (T,R)         [enforced, conditional on (T)]
```

#### Which edge drives — the rule, in order

1. **One** internal enforced edge → it drives.
2. Exactly one edge marked `drives: true` → it drives.
3. No marker, with **at least two DISTINCT candidate parents**: the
   parent that **descends from every other candidate parent** drives,
   and its edge to the other parent is widened with the child's pins
   (`fk_edge_widened`).
4. No marker, and either no ancestry between the parents **or only ONE
   distinct candidate parent** (two enforced edges to the SAME parent —
   final review, ADR 0037: rule 3's ancestry check ran over an empty
   set of "other" parents and was vacuously true, mislabelling this case
   `"derived"`) → the **first declared** internal enforced edge drives.
   The launcher logs `fk_driving_edge_defaulted table= edge= hint='mark
   drives: true to choose'` at **WARNING** and the card tags it `DRIVES
   (first declared — mark drives: true to choose)`. Reorder the `fk:`
   list or add `drives: true` to choose a different one. The edge chosen
   is the same either way; only the label and the WARNING changed.
5. More than one `drives: true` on one table → `RelationshipError`.
6. Two **non-driving** edges writing the same child column →
   `RelationshipError`. See "Two non-driving edges cannot write the same
   column" below.

`drives: true` is always the override; toggling `enabled` is still
enough to launch.

#### Two non-driving edges cannot write the same column

`edge_roles` gives every non-driving edge a role from its overlap with
the DRIVING edge alone — it never compared two non-driving edges with
EACH OTHER, so two of them could claim the same child column and the
last one drawn silently won: two `independent` edges each write a whole
pool tuple in declaration order, so the second destroys the first's
columns and nearly every row is diverted as `fk.orphan` — after the GPU
already generated it; two `conditional` edges whose `rest` overlaps are
applied in plan order and `conditional` edges are not gated by
`fk.orphan` at all, so those referentially broken rows LAND uncaught.
`RelationshipRegistry._check_column_ownership` (final review, ADR 0037)
now raises on the first such pair:

```text
child: edges (X,Y)->pa [independent] and (X,Z)->pb [independent] both write (X) — one child column cannot be owned by two edges: the second draw overwrites the first, landing a tuple its parent never held. Make one edge's columns a SUBSET of the other's so it is implied, mark the edge this table is generated from `drives: true`, or disable one parent (`enabled: false`).
```

Three ways out, matching the message:

1. Make one edge's columns a **subset** of the other's, so the registry
   resolves it as `implied` instead of a second independent write.
2. Mark the edge this table is actually generated from `drives: true`.
3. **Disable** one of the two parents (`enabled: false`).

The DRIVING edge itself is deliberately outside this check — `implied` /
`independent` / `conditional` are disjoint from it by construction (the
role IS the overlap test with the driving edge). An `external` edge that
overlaps the DRIVING edge stays the pre-existing, un-stopped
`fk_edge_overlap_external` WARNING (design 2026-09-11 §9) — not this
stop.

**Out of scope for now:** resolving the clash automatically instead of
stopping the launch, via a candidate/pool-level join on the columns the
two parents share — that would let both non-driving edges draw jointly
from the intersection instead of one silently overwriting the other.
Not implemented; recorded as future work in
[ADR 0037](../../docs/adr/0037-multi-parent-children.md) Consequences.

#### `--fk_candidate_cap` (default 64)

A conditional edge keeps at most `M = --fk_candidate_cap` candidate
tuples per shared value (a deterministic hash-ordered sample), so a hot
shared key never carries an unbounded list into a request. A key whose
fan-out exceeds the candidates it was handed **wraps** — it reuses
them, in a seeded order. Raising the cap only buys back the wrapping the
cap itself caused: a shared value the parent simply has too few distinct
candidates for wraps at any cap. See the figure in
[ADR 0037](../../docs/adr/0037-multi-parent-children.md) (D4).

#### When the parent has no candidate for a key (ruling B)

A driving key whose shared value does not exist in the conditional
parent at all:

- **every `rest` column NULLABLE** in the landing schema → the engine
  writes `NULL` there. The row lands, legitimately parentless (the
  orphan query excludes NULL tuples, as it always has).
- **otherwise** → the key is dropped **before** generation (no GPU spend
  on a row that cannot be valid), counted as `fanout/keys_unmatched`,
  and reported as one DLQ envelope per key, `rule_id="fk.unmatched"`,
  weighted by that key's expected rows so the BLOCKER gate sees the
  rows that were lost. An empty `rest` (existence filter) is never
  nullable, so its unmatched keys are always dropped.

`fk.unmatched` is NOT `fk.orphan`: an orphan is a generator regression
and never expected, while an unmatched key means the SOURCE lacks that
branch.

#### Worked example — a star fact

Two dimensions with no ancestry between them
(`config/relationships/example_star_diamond.yaml`):

```yaml
  dim_a:
    pk: [A_KEY]
  dim_b:
    pk: [B_KEY]
  fact:
    pk: [A_KEY, B_KEY, LINE_NO]
    fk:
      - cols: [A_KEY]   # first declared -> DRIVES (rule 4)
        ref: dim_a
        ref_cols: [A_KEY]
      - cols: [B_KEY]   # no shared column -> independent
        ref: dim_b
        ref_cols: [B_KEY]
```

```text
 wave 2 | fact                     pk(A_KEY,B_KEY,LINE_NO)
        |   +- (A_KEY) --> dim_a (A_KEY)   [enforced, DRIVES (first declared — mark drives: true to choose)]
        |   +- (B_KEY) --> dim_b (B_KEY)   [enforced, independent]
```

Add `drives: true` to the `dim_b` edge and the WARNING goes away with
the roles swapped.

#### Worked example — a true diamond

`bottom` reaches `top` through both `left` and `right`, so `T` must be
one value satisfying both:

```yaml
  top:
    pk: [T]
  left:
    pk: [T, L]
    fk: [{cols: [T], ref: top, ref_cols: [T]}]
  right:
    pk: [T, R]
    fk: [{cols: [T], ref: top, ref_cols: [T]}]
  bottom:
    pk: [T, L, R]
    fk:
      - cols: [T, L]    # DRIVES: bottom is generated from left's keys
        ref: left
        ref_cols: [T, L]
        drives: true
      - cols: [T, R]    # shares T -> conditional on (T); R is joined in
        ref: right
        ref_cols: [T, R]
```

```text
 wave 3 | bottom                   pk(T,L,R)
        |   +- (T,L) --> left (T,L)   [enforced, DRIVES]
        |   +- (T,R) --> right (T,R)   [enforced, conditional on (T)]
```

`T` and `L` are inherited from the driving key; `R` is drawn from the
`right` rows that actually carry that `T`. The two branches are drawn
independently given `T` — the source's joint `(L, R)` distribution is
not reproduced (ADR 0037, Consequences).

Render either shape without launching:

```bash
uv run --no-sync python3 scripts/relationships/card.py \
  --relationships-uri config/relationships/example_star_diamond.yaml --all
```

## What a launch does with it

| Target(s) | `--generate_fk_relationships` | Result |
|---|---|---|
| one table | `false` | that table only; any enforced edge is loudly ignored |
| one table | `true` (default) | the table's whole **enabled** component, parents first, in ONE Dataflow job (ADR 0030) |
| many tables | `false` | each independently — concurrent isolated generation |
| many tables | `true` | the union of their components. Rare and expensive; the launcher says so |

A table that appears in no model file generates alone, with `--pk_cols` /
`--identity_cols` for its keys — zero config for one-off tables. A table
that IS in a model takes its keys from the model, and a conflicting
`--pk_cols` is ignored with a WARNING.

## `pk:` must actually BE a key

Preflight measures the declared PK against the reference sample and
**stops the launch** when it repeats on more than half the rows: the run
would land about as many rows as the tuple has distinct values and divert
the rest as `pk.duplicate` (2026-08-25: 99.4% duplicates in the sample →
74 rows landed of 1,000,000, BLOCKER gate tripped 11 minutes and one GPU
later). Fix the `pk:` (usually a missing discriminating column), lower
`--num_rows`, or move the column to `identity:` if it was never a key.

A column listed under `identity:` that ALSO carries a `pattern` (or any
clause the router can sample) is generated from that clause — unique per
run, never a source value — instead of a UUID. The launcher logs
`identity_constraint_owned` when that happens.

## Rules the loader enforces at launch

- every `fk.ref` names a table in the model, or is `dataset.table`
- `cols` and `ref_cols` have the same arity, and neither is empty
- a table belongs to exactly ONE model file
- one `fk:` entry per `(cols, ref, ref_cols)` on a table — a duplicate
  entry is a parse error (`declared twice`), because "the first declared
  edge drives" must not depend on which copy you meant
- enforced edges form a DAG (no cycles)
- every column named by `pk` / `identity` / an enforced `fk.cols` exists in
  the real table (preflight P2 — this is where a YAML typo stops the run,
  before any GPU spend)

A file that exists but does not parse **stops the launch**. An empty or
missing folder is fine: it means "no relationships declared anywhere".

## Real names stay out of git

Everything here is committed, so everything here uses aliases
(`X_TABLE` / `COL_XXX`). The folder is gitignored apart from
`*.example.yaml` and this README, so a real model file dropped next to
them ships in your image build and can never be committed by accident.
The `gs://` override keeps real names off the filesystem entirely.

## After editing

```bash
# see what a launch will do, without launching
uv run --no-sync python3 scripts/relationships/card.py --table A_TABLE

# the committed samples (a directory scan skips them; name the FILE)
uv run --no-sync python3 scripts/relationships/card.py \
  --relationships-uri config/relationships/example_star_diamond.yaml --all

# check every table the models reference actually exists
uv run --no-sync python3 scripts/deployment_prerequisites.py --project <p>
```
