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
grandparent. `drives: true` is only needed when no parent descends from
the others, and the launch says so.

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
  it. An edge that is neither driving nor implied is a preflight stop
  naming the exact edit — widen the driving parent's edge to carry the
  missing columns, or mark the ambiguous edge `drives: true` yourself.

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

# check every table the models reference actually exists
uv run --no-sync python3 scripts/deployment_prerequisites.py --project <p>
```
