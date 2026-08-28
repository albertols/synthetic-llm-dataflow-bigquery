---
mode: agent
description: >
  Resolve the relational model from config/relationships/ (ADR 0032 —
  PK, FK edges enforced vs documented, identity, enabled/disabled) and
  the per-column llm_prompt_constraint clauses from the landing
  dataset's COLUMN descriptions (default: synthetic_data), then produce
  a timestamped VISUAL contract-guide snapshot:
  the relationship card + mermaid diagram, per-table facts, and a
  closing table of every table.field's description + constraint clause.
  Written twice: integration_tests/ddl_contract_guides/<STAMP>/real/
  (verbatim names, local-only) and .../oss/ (aliases from the history
  mappings registry, shareable, leak-scanned). ADC access to the target
  GCP project is a PREREQUISITE and is verified first. Parsing and graph
  resolution ALWAYS go through the repo's own tooling
  (sdfb_core.contracts.*) — never hand-parse a model or a clause.
---

# /visual_fk_pk_ddl_contract_guide — Visual FK/PK DDL-contract snapshot

## Goal

Produce a **visual, self-contained snapshot of the contract layer** — the
relational model as declared in `config/relationships/`, and the column
steering as deployed on the landing tables — so anyone can answer at a
glance: which tables the model covers, their PK / identity / FK edges
(enforced solid, documented dashed, disabled marked), what every
`llm_prompt_constraint` says, and what every field's description is.

Output folder (timestamp = generation time, `date +%Y_%m_%d_%H_%M`):

```
integration_tests/ddl_contract_guides/<YYYY_MM_DD_HH_MM>/
  real/ddl_contract_guide.md     # verbatim names — INTERNAL, local-only
  oss/ddl_contract_guide.md      # registry aliases — shareable
```

`integration_tests/` is gitignored: `real/` is safe by construction and
must NEVER be committed or shared; `oss/` is the hand-off artifact.

This is a **repeated action** (re-run after every Terraform contract
change). Be deterministic, show what the dataset actually declares,
never invent an edge, and keep everything **generic** — no project,
dataset, table, or column name is hard-coded.

---

## Inputs (ask the user if not provided; nothing is hard-coded)

| Param | Example | Notes |
|---|---|---|
| `PROJECT` | `<project-id>` | GCP project id |
| `LANDING_DATASET` | `synthetic_data` | dataset whose COLUMN descriptions carry the constraints (ADR 0027 D2: they live on the LANDING tables) |
| `RELATIONSHIPS` | `config/relationships` | the relational models (ADR 0032) — folder, file, or `gs://`. The ONLY source of PK/FK/identity |
| `OUT_ROOT` | `integration_tests/ddl_contract_guides` | snapshot parent dir |
| `REGISTRY` | `integration_tests/history_mappings_replacement.json` | history-mappings alias registry (ADR 0029 D6) — created/extended if absent |

---

## Step 0 — PREREQUISITE: verify GCP access (ADC)

```bash
gcloud auth application-default print-access-token >/dev/null \
  && echo "ADC OK" || echo "ADC MISSING — run: gcloud auth application-default login"
bq --project_id "$PROJECT" ls "$LANDING_DATASET" | head -5
```

Stop and tell the user if either fails. Everything below is read-only
against BigQuery metadata (INFORMATION_SCHEMA) — no table data is read.

## Step 1 — Scan ALL descriptions in the dataset (two queries, no loops)

```bash
# Table descriptions are PROSE only now (ADR 0032) — collected for the
# per-table sections, never parsed for relational structure.
bq query --project_id="$PROJECT" --use_legacy_sql=false --format=json "
  SELECT table_name, option_value AS description
  FROM \`$PROJECT.$LANDING_DATASET\`.INFORMATION_SCHEMA.TABLE_OPTIONS
  WHERE option_name = 'description'" > /tmp/table_descriptions.json

bq query --project_id="$PROJECT" --use_legacy_sql=false --format=json "
  SELECT table_name, column_name, field_path, data_type, description
  FROM \`$PROJECT.$LANDING_DATASET\`.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS
  ORDER BY table_name, column_name" > /tmp/column_descriptions.json
```

A table the models do not declare is still listed (as a model-less node)
— absence is a finding, not an omission. So is the reverse: a table the
MODEL declares that does not exist in the dataset.

## Step 2 — Resolve the model + clauses with REPO TOOLING ONLY

Never hand-parse. The repo owns one model loader and one clause parser —
use them so this snapshot can never disagree with what the pipeline does
(`uv run --no-sync python3` from the repo root):

```python
import json
from pathlib import Path

from sdfb_core.contracts.prompt_constraint import (
    parse_llm_prompt_constraint, parse_prompt_constraint,
)
from sdfb_core.contracts.relationships import RelationshipRegistry

# The relational model — the single source of truth (ADR 0032).
base = Path(RELATIONSHIPS)
paths = sorted(base.glob("*.y*ml")) if base.is_dir() else [base]
registry = RelationshipRegistry.from_sources(
    [(str(p), p.read_text(encoding="utf-8")) for p in paths]
)
sha = registry.sha12()
for model in registry.models:
    first = next(iter(model.tables))
    card = registry.card(first)          # the launcher's own rendering
    mermaid = registry.mermaid(first)    # the house-style diagram

tables_desc = {r["table_name"]: (r.get("description") or "").strip("\"' ")
               for r in json.load(open("/tmp/table_descriptions.json"))}
```

`registry.card(table)` is EXACTLY what a launch logs — reuse it, never
re-render it. `registry.component(table)` is what a scenario-2 launch on
that table would generate; `registry.generation_waves(...)` is the order.

Per column, collect `route`, the rendered clause
(`parse_llm_prompt_constraint(description, column=name)`), and EVERY
structured field of `parse_prompt_constraint(...)` — `pattern`,
`values`, `length`, `families` (ADR 0028 Tier-P shares), `prefix`,
`suffix`, `charset`, `units`, `locale`, `examples`, `notes` — they feed
the closing table's `route` + `clause fields` columns. A
marked-but-unparseable JSON is a LOUD finding (quote the
`DescriptionJsonError`), never a silent skip.

## Step 3 — Write `real/ddl_contract_guide.md`

Document contract, in this order:

1. **Header** — dataset FQN, the model FILE(s) read, snapshot timestamp,
   `sha=<registry.sha12()>`, table/edge counts.
2. **The model, twice** (both are MANDATORY):
   - `registry.card(table)` verbatim inside a ```text fence — the same
     card the launcher logs (waves, `pk(...)`, `identity(...)`,
     `-->` enforced / `..>` documented, `[DISABLED — detached]`);
   - `registry.mermaid(table)` inside a ```mermaid fence — the rendered
     picture. Do NOT redraw or restyle either.
3. **Per-table facts** — one subsection per table: `pk` (composite order
   preserved), `identity`, each FK edge as `(cols) --> ref (ref_cols)`
   with `[documented]` where `enforced: false`, `[DISABLED]` where the
   table is detached, constrained-column counts split by route
   (`N forced route=llm / M auto`), and a `⚠ not in any model` marker
   for dataset tables no model declares (they generate alone, with
   PK/identity from CLI flags).
4. **Components** — list each component's tables (`registry.component`):
   this is exactly what a scenario-2 launch of any member would
   generate. Flag any component containing near-duplicate table names
   (e.g. legacy twins of renamed tables) — the 2026-08-22 run
   double-generated because stale twins stayed in the dataset. Also flag
   any table the MODEL declares that is missing from the dataset.
5. **Closing field table** (MANDATORY, every table, every field —
   identical column set in `real/` and `oss/`):

   | table.field | type | route | clause fields | description (prose) | llm_prompt_constraint (rendered) |
   |---|---|---|---|---|---|

   - `route` = the DECLARED routing: `llm` (forced onto the free-text
     path) or `auto` (typed classification decides); empty when the
     field carries no constraint. This column is mandatory — it is the
     difference between a clause that DRIVES generation and one that
     only steers (DDL_CONTRACT_GUIDE §4 routing).
   - `clause fields` = the structured keys actually set, compact, in
     this order (omit unset ones):
     `pattern ✓ · values=N · length=X[-Y] · families=N · prefix=… ·
     suffix=… · charset=… · units=… · locale=… · examples=N`.
     These are the §4 metadata fields — `pattern` and `families` decide
     Tier-P sampling (ADR 0028), `values` the enum domain, `length`
     the pin that suppresses the derived hint.
   - `description (prose)` = the column description with the embedded
     `{"llm_prompt_constraint": …}` JSON removed (prose before/after
     the brace-balanced object, per DDL_CONTRACT_GUIDE §2).
   - `llm_prompt_constraint (rendered)` = the repo-rendered clause
     (`parse_llm_prompt_constraint`) verbatim; empty cell when
     unconstrained. Escape `|` inside clauses as `\|`.

## Step 4 — Write `oss/ddl_contract_guide.md` via the alias registry

Same document (IDENTICAL structure and column set), aliased. The
replacements MUST come from the persistent registry —
`integration_tests/history_mappings_replacement.json` — and from
nowhere else: the same real table/column maps to the SAME alias here,
in every `e2e_bundle_export` oss/ bundle and in every validation
report (ADR 0029 D6 — the registry is the single cross-artifact decode
key). Reuse existing entries; `assign_table` is idempotent and only
APPENDS unseen tables/columns (new tables continue G_TABLE, H_TABLE, …
past the seeded block). Never invent an alias, never renumber:

```bash
# once per real table (idempotent; appends new tables as G_TABLE, H_…):
uv run --no-sync python3 scripts/e2e/history_mappings.py assign \
  --registry "$REGISTRY" --real-fqn "$PROJECT.$LANDING_DATASET.<table>" \
  --ddl-json <(bq show --schema --format=prettyjson ... | jq '{schema: .}')
```

(Or load `HistoryMappings` in the same python session and call
`assign_table(fqn, [column names in DDL order])` per table — simpler.)
Replace every table name and every column name in the document —
diagrams, per-table sections, the closing field table, AND inside
rendered clause text / prose descriptions (a `notes` clause may name a
sibling column) — with the registry aliases. Fields recorded under
`retained` pass through as-is. `route` and the structured clause
fields are metadata, not identifiers: they appear UNCHANGED in oss/.
Then **leak-scan**: grep the oss/ file for every `real_fqn` and every
real column name present in the registry; any hit = fix before
delivering. Never write real names into `oss/`.

If the dataset's tables are ALREADY alias-named (post-rename
deployments), `real/` and `oss/` will largely coincide — still write
both; the leak scan is the proof, not the assumption.

## Step 5 — Recycle the diagram

Write the mermaid source to `integration_tests/fk_models/<sha>.mmd`
(create the dir if needed) unless that file already exists — the E2E
validation report (prompt §5.5) embeds it by sha instead of redrawing.

## Consistency rules (always enforce)

- Repo tooling only for parsing/graphing — a snapshot that disagrees
  with `sdfb_core.contracts` is worse than none.
- Enforced vs documented is a CONFIG fact (`enforced: false`), and
  detached is another (`enabled: false`) — never a judgment call;
  dashed/`..>` everywhere for documented, `[DISABLED]` for detached.
- Both renderings (card + mermaid) in both files; the closing field
  table is never truncated — every field of every table appears.
- `real/` stays local (gitignored); only `oss/` may leave the machine,
  and only after a clean leak scan.
- Relative links; no dashboards / Vertex / external LLM suggestions
  (out of scope per `CLAUDE.md`).
