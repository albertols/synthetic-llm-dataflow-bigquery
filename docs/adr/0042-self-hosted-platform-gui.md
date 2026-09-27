# ADR 0042 — A self-hosted platform GUI may read this project's BigQuery tables; managed dashboard services stay out

**Status:** ACCEPTED (2026-09-27). The project owner decided this when asking for the GUI. The GUI itself is built on `feat/gui-synthetic-platform`, and the acceptance criteria below close when that branch merges.
**Design:** [`docs/DESIGN.md` §12 Platform GUI](../DESIGN.md#12-platform-gui)
**Amends:** [ADR 0001](0001-no-managed-gcp-services.md). Its Decision ("no Looker Studio dashboards") and its Forbids line ("visualize in Looker Studio") are narrowed to managed dashboard services. A self-hosted app may now visualize the project's tables.
**Keeps:** ADR 0001's serving path (LLM inference inside Beam `DoFn`s; no Vertex AI, no external LLM APIs) and its data-quality rule (results land in BigQuery and GCS; no Dataplex) · [ADR 0040](0040-dsg-donation-golden-source-sync.md)'s manifest (the GUI is not donated)

## Context

Three places said no to anything visual:

- CLAUDE.md hard constraint 3: "No Dataplex, no Looker. … Do not propose dashboards or DQ scans."
- ADR 0001 forbids design proposals that "visualize in Looker Studio". It also says the ADR "must be superseded explicitly" if that ever becomes a real requirement.
- The ROADMAP's M2 non-goal: "Dataplex / Looker dashboards (ADR-0001 still applies)."

The reason for the rule was portability. ADR 0001 kept managed services
out because many adopters cannot provision them, either by policy or because
of cost. A dashboard product is one more service to approve, license and give
data access to. The rule never said that pictures of the data were harmful.

Meanwhile, the project collected data that is hard to read as rows or as
static PNGs:

- the evaluation tables and views, frozen in `packages/sdfb-evaluation` (`evaluation_metrics`, `evaluation_profiles`, `evaluation_row_flags`, `evaluation_data_history`, `evaluation_latest*`);
- per-run validation (`synthetic_data_quality.validation_runs`, `dlq`, `fk_fanout_stats`) and source statistics (`synthetic_rag.source_table_stats`);
- the RAG embedding space and the generation knobs, which are explained today only by committed figures and by the code.

On 2026-09-27 the project owner asked for a GUI that reads and explains this
data. They chose to record it as a new ADR with a narrower rule, not as an
exception to the old one.

## Decision

| Allowed by this ADR | Still out (ADR 0001, CLAUDE.md constraint 3) |
| :-- | :-- |
| A self-hosted app in `gui/`, run locally or on Cloud Run behind IAP | Looker, Looker Studio / Data Studio, any managed dashboard service |
| Named, parameterized, read-only `SELECT`s under a bytes cap | Raw SQL from the browser; any write from the GUI |
| Reading the project's own `synthetic_data_quality.*` and `synthetic_rag.*` tables | Dataplex data-quality scans and profiling |
| A mock mode that uses invented or public names | Fetched rows persisted to disk or committed |

**D1 — A self-hosted GUI may read this project's BigQuery tables.** It lives
in `gui/`, as a TypeScript browser app plus a small backend-for-frontend
(BFF). It runs locally first. The BFF binds `127.0.0.1` and queries BigQuery
with the
[Application Default Credentials](https://docs.cloud.google.com/docs/authentication/application-default-credentials)
of the person running it. The same container may later run on Cloud Run
behind
[Identity-Aware Proxy](https://docs.cloud.google.com/iap/docs/enabling-cloud-run).
That deployment is optional, and the pipeline never requires it.

**D2 — Read-only named queries with a bytes cap.**

- The browser never sends SQL. It names a query registered in the BFF and passes typed [query parameters](https://docs.cloud.google.com/bigquery/docs/parameterized-queries). The BFF refuses a name it does not know.
- Every registered query is a `SELECT`. The BFF dry-runs it, then runs it with the maximum bytes billed set ([restrict the number of bytes billed per query](https://docs.cloud.google.com/bigquery/docs/best-practices-costs)). The cap defaults to 10 GB, and the UI shows the estimate.
- The identity the BFF runs as needs only [BigQuery Data Viewer and Job User](https://docs.cloud.google.com/bigquery/docs/access-control). Locally that identity is the developer's own ADC, which may hold more, so read-only rests on the named `SELECT`s. On Cloud Run the service account holds only those two roles, so IAM enforces read-only as well. The GUI writes nothing to BigQuery or GCS.
- Credentials stay in the BFF and never reach the browser. This is the Backend for Frontend architecture of [RFC 10017 §6.1, *OAuth 2.0 for Browser-Based Applications* (IETF, 2026)](https://www.rfc-editor.org/rfc/rfc10017#section-6.1), where tokens stay on the server and the browser holds none.
- Fetched rows are cached in memory in a bounded LRU cache and are never persisted.

**D3 — Managed dashboard services stay out.** Looker, Looker Studio (renamed
[Data Studio in April 2026](https://docs.cloud.google.com/data-studio/welcome))
and Dataplex data-quality scans and profiling remain out, for ADR 0001's
reason. Validation results still land only in `synthetic_data_quality.*`
tables and GCS artifacts. The GUI reads those results; it is not a new place
where results land, and the pipeline never depends on it. The GUI calls no
LLM, so constraint 4 (no external LLM APIs) still holds.

**D4 — The GUI is not part of the DSG donation.** `dsg/manifest.yaml`'s
`include` matches nothing under `gui/` or `scripts/gui/`. If a future glob
would match one of those paths, the path goes under `exclude`.
`docs/DESIGN.md` does ship ([ADR 0040](0040-dsg-donation-golden-source-sync.md) A2),
so its §12 states that the GUI stays in this repository. ADR 0040 D3 pins
that section's code link to this repository.

**D5 — The Python side stays the source of truth.** The GUI's types are
generated from the Python side: the BigQuery JSON schemas, the evaluation
metric catalogue and the exported knobs. CI fails when the generated types
drift from those sources. Where the docs and the code disagree, the GUI shows
what the code does. The mock mode (`DATA_SOURCE=mock`) serves seeded fixtures
with invented or public `thelook` names only. The GUI therefore runs on a
laptop with no GCP access, and the sensitive-content precheck covers `gui/`
like any other path.

## Consequences

- **Enables:** a run's evaluation, validation and source statistics can be read side by side, runs can be compared, and the RAG space and the knobs can be explored visually. None of this needs a new managed service, and mock mode works offline.
- **Costs:**
  - A second toolchain (Node 22, npm workspaces), with its own CI workflow, dependency updates and bundle budgets.
  - A contract-drift gate between Python and TypeScript.
  - Query spend, bounded per query by the cap.
  - Trademark care. The Apache Beam name and logo refer to Beam only and never appear in the product name. Google Cloud icons follow Google's icon terms and are attributed in `gui/ATTRIBUTION.md`.
- **Forbids:**
  - raw SQL from the client;
  - a write path from the GUI;
  - persisting fetched rows;
  - a pipeline or DSG dependency on `gui/`;
  - Looker, Looker Studio / Data Studio and Dataplex, as before.

## Alternatives rejected

- **A static documentation site only** ([mdBook](https://rust-lang.github.io/mdBook/) or similar; ROADMAP M3). A site renders the committed Markdown and figures. It cannot read a run's tables, compare two runs, or show what a knob does to live numbers. It stays on the M3 list as a way to publish `docs/`, not as the GUI.
- **Notebooks (Jupyter).** They are quick to start, but they break D2:
  - A notebook stores its cell outputs inside the `.ipynb` file ([the notebook file format](https://nbformat.readthedocs.io/en/latest/format_description.html)), fetched rows included. D2 forbids persisting rows, and the precheck exists to stop committed data.
  - Every viewer can run arbitrary SQL, with no named-query contract and no bytes cap.
  - Notebook diffs are hard to review.
- **Looker Studio (now Data Studio).** It connects to BigQuery directly ([connector](https://docs.cloud.google.com/data-studio/connect-to-google-bigquery)), but it is the managed service that ADR 0001 keeps out:
  - Adopters who cannot provision it would lose the view.
  - In its default owner's-credentials mode, report viewers see data they hold no BigQuery role for.
  - Its reports live outside this repository, unversioned and unreviewed.

## Acceptance criteria

1. `scripts/dsg/sync.py` stages no path under `gui/` or `scripts/gui/`.
2. The BFF refuses a query name it has not registered. Every registered query is a single read-only `SELECT` and runs with the maximum bytes billed set. Both properties are unit-tested against a fake BigQuery client.
3. With `DATA_SOURCE=mock`, the GUI starts and every tab renders without GCP credentials.
4. The BFF listens on `127.0.0.1` unless it is configured otherwise. No response body or header carries a credential.

Sources retrieved 2026-09-27.
