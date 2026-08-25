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
        enforced: true                # default true — see "Two flags"
        note:     optional prose
```

`ref` names a table in the same model (bare name) **or** an already-landed
parent outside it (`dataset.table` — dataset-qualified is what marks it
external). `ref_cols` need not be the parent's full PK: any projection
works, because the child draws from `SELECT DISTINCT ref_cols` of the
parent's landed rows.

## Two flags, two different jobs

| Flag | Where | Meaning |
|---|---|---|
| `enabled: false` | table | **Detach.** The table leaves the graph; anything that reached the rest of the model only through it detaches with it. It still generates when you target it directly — the flag governs participation, not permission. |
| `enforced: false` | edge | **Document only.** The relationship is real and appears in the card and the diagram, but no keys are drawn from it and `fk.orphan` has nothing to check. For join keys that exist in the business model and not in the DDL. |

Worked example: `A ← B ← C`. Set `enabled: false` on `B` and a launch on
`A` generates `A` alone — `C` reached `A` only through `B`.

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
