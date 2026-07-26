# WS4 — Deploy-Speed Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deploy loop fast to iterate: `--write_disposition {append,overwrite}` on the landing sink, live `INFORMATION_SCHEMA` schema extraction when `--ddl_uri` is absent, and `--create_if_not_exists` mapping to `CREATE_IF_NEEDED` with the in-pipeline derived landing schema.

**Architecture:** All behavior lands in `run_pipeline.py` (argparse + sink construction + a schema-resolution precedence function) and `sdfb_beam/ddl/extractor.py` (a `TableSchema`-returning wrapper over the existing `extract_ddl_metadata`). The deploy chain (Flex-template metadata JSON, Composer DAG, GH workflow 3, personal-GCP `tiers.yaml`) is threaded afterwards so every launch path can use the new flags and the happy path stops staging `_ddl.json` in GCS.

**Tech Stack:** argparse + apache-beam `WriteToBigQuery` dispositions; `google.cloud.bigquery` (lazy, injectable client) for live extraction; JSON/YAML deploy artifacts; pytest.

**Spec:** `docs/superpowers/specs/2026-07-20-e2e-remediation-rag-eval-evolution-design.md` §6 (WS4: 6a/6b/6c). Locked decision §1.6: landing write disposition defaults to `append` (today's behavior); `--create-if-not-exists` maps to `CREATE_IF_NEEDED` with the in-pipeline derived landing schema. Precedence (§1.5): explicit `--ddl_uri` (pin/air-gap) > live extraction.

## Global Constraints

- **Blast radius (6a/6c):** dispositions change the **landing** sink only. DLQ, `validation_runs`, `rag_chunks` stay `WRITE_APPEND` + `CREATE_NEVER` unconditionally. Quality/RAG tables are never auto-created.
- `overwrite` maps to `BigQueryDisposition.WRITE_TRUNCATE` (FILE_LOADS-compatible); default `append` = `WRITE_APPEND` — byte-for-byte today's behavior when no new flag is passed.
- `CREATE_IF_NEEDED` must carry `derive_bq_schema(table_schema)` (`sdfb_core.codegen`, returns `{"fields": [...]}`); off ⇒ today's `CREATE_NEVER` with no schema kwarg.
- Live extraction happens at graph-construction time on the driver (same lifecycle stage as the live reference read), reusing `extract_ddl_metadata(project, dataset, table, *, timeout, client)` (`sdfb_beam/ddl/extractor.py:36`) — no new extraction code path.
- Flex Template parameters are STRINGS: boolean-ish flags are string-valued (`"false"` default) and parsed with a truthy set `{"true", "1", "yes"}` (case/whitespace-insensitive).
- No silent behavior: schema source and landing dispositions are announced via `log_milestone` (`ddl_loaded_from_uri` / `ddl_live_extracted` / `landing_sink_config`).
- Branch: `ws4-deploy-speed`, created FROM `ws2-rag-phase-a` (stacked on PR #4 — WS2 also edited `run_pipeline.py`). Use superpowers:using-git-worktrees at execution start.
- Test command: `uv run --no-sync python3 -m pytest <path> -q` (**always `--no-sync`**). Baseline `uv run --no-sync python3 -m pytest packages/sdfb-tests -m "not gpu and not gcp" -q` must stay green (515 at branch point). Lint `uv run --no-sync ruff check .` clean after every task.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Docs sweep (RUN_PLAYBOOK, DEPLOYMENT_PREREQUISITES, skills, ROADMAP) is **WS5** — do not touch docs here beyond code docstrings/help text.

---

### Task 1: `--write_disposition` + `--create_if_not_exists` on the landing sink (6a + 6c)

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (argparse ~line 75; new helpers after `resolve_engine_strictness`; landing-sink block at ~line 353)
- Test: `packages/sdfb-tests/tests/unit/cli/test_run_pipeline.py` (append)

**Interfaces:**
- Consumes: `derive_bq_schema(table_schema: TableSchema) -> dict` from `sdfb_core.codegen` (exists); `BigQueryDisposition` (already imported).
- Produces (used by Task 4's deploy threading): argparse args `write_disposition` (choices `append`/`overwrite`, default `append`) and `create_if_not_exists` (string, default `"false"`); `parse_bool_flag(value: str) -> bool`; `resolve_landing_dispositions(write_disposition: str, create_if_not_exists: bool) -> tuple[str, str]`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/sdfb-tests/tests/unit/cli/test_run_pipeline.py` (extend the existing `from sdfb_beam.cli.run_pipeline import (...)` block with `parse_bool_flag, resolve_landing_dispositions`):

```python
def test_parse_args_write_disposition_default_and_choices():
    args, _ = parse_args(_common_args())
    assert args.write_disposition == "append"
    assert args.create_if_not_exists == "false"
    args, _ = parse_args([*_common_args(), "--write_disposition", "overwrite"])
    assert args.write_disposition == "overwrite"
    with pytest.raises(SystemExit):
        parse_args([*_common_args(), "--write_disposition", "truncate"])


def test_parse_bool_flag_truthy_set():
    assert parse_bool_flag("true")
    assert parse_bool_flag("1")
    assert parse_bool_flag(" YES ")
    assert not parse_bool_flag("false")
    assert not parse_bool_flag("0")
    assert not parse_bool_flag("")
    assert not parse_bool_flag("no")


def test_resolve_landing_dispositions_matrix():
    from apache_beam.io.gcp.bigquery import BigQueryDisposition

    assert resolve_landing_dispositions("append", False) == (
        BigQueryDisposition.WRITE_APPEND,
        BigQueryDisposition.CREATE_NEVER,
    )
    assert resolve_landing_dispositions("overwrite", False) == (
        BigQueryDisposition.WRITE_TRUNCATE,
        BigQueryDisposition.CREATE_NEVER,
    )
    assert resolve_landing_dispositions("append", True) == (
        BigQueryDisposition.WRITE_APPEND,
        BigQueryDisposition.CREATE_IF_NEEDED,
    )
    assert resolve_landing_dispositions("overwrite", True) == (
        BigQueryDisposition.WRITE_TRUNCATE,
        BigQueryDisposition.CREATE_IF_NEEDED,
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli/test_run_pipeline.py -q`
Expected: FAIL — `ImportError: cannot import name 'parse_bool_flag'`.

- [ ] **Step 3: Implement in `run_pipeline.py`**

Add the two arguments right after the `--dlq_table` argument:

```python
    p.add_argument("--write_disposition", default="append",
                   choices=["append", "overwrite"],
                   help="Landing-table write mode. append = WRITE_APPEND "
                        "(default, today's behavior); overwrite = "
                        "WRITE_TRUNCATE (FILE_LOADS-compatible). DLQ, "
                        "validation_runs and rag_chunks always append.")
    p.add_argument("--create_if_not_exists", default="false",
                   help="true/1/yes: create the landing table on first "
                        "write (CREATE_IF_NEEDED) carrying the landing "
                        "schema derived in-pipeline from the DDL. Anything "
                        "else: CREATE_NEVER (default). Quality/RAG tables "
                        "are never auto-created.")
```

Add the helpers after `resolve_engine_strictness`:

```python
# Flex Template parameters are strings; this is the accepted truthy set for
# string-valued boolean flags threaded through the template/DAG chain.
_TRUTHY_FLAG_VALUES = frozenset({"true", "1", "yes"})


def parse_bool_flag(value: str) -> bool:
    """Normalize a string-valued boolean Flex-Template parameter."""
    return str(value).strip().lower() in _TRUTHY_FLAG_VALUES


def resolve_landing_dispositions(
    write_disposition: str, create_if_not_exists: bool
) -> tuple[str, str]:
    """Landing-sink ``(write, create)`` dispositions (WS4 §6a/§6c).

    Landing ONLY — the DLQ, validation_runs and rag_chunks sinks stay
    WRITE_APPEND/CREATE_NEVER unconditionally (blast-radius rule)."""
    write = (
        BigQueryDisposition.WRITE_TRUNCATE
        if write_disposition == "overwrite"
        else BigQueryDisposition.WRITE_APPEND
    )
    create = (
        BigQueryDisposition.CREATE_IF_NEEDED
        if create_if_not_exists
        else BigQueryDisposition.CREATE_NEVER
    )
    return write, create
```

In `main()`, replace the `landing_sink = WriteToBigQuery(...)` block (currently hard-coded APPEND/NEVER) with:

```python
    create_if_not_exists = parse_bool_flag(args.create_if_not_exists)
    landing_write, landing_create = resolve_landing_dispositions(
        args.write_disposition, create_if_not_exists
    )
    landing_kwargs: dict = {}
    if create_if_not_exists:
        # CREATE_IF_NEEDED must carry the target schema — derived
        # in-pipeline from the resolved TableSchema (WS4 §6c), never
        # hand-provisioned.
        landing_kwargs["schema"] = derive_bq_schema(table_schema)
    log_milestone(
        "landing_sink_config",
        write_disposition=landing_write,
        create_disposition=landing_create,
    )
    landing_sink = WriteToBigQuery(
        table=args.landing_table,
        method=WriteToBigQuery.Method.FILE_LOADS,
        write_disposition=landing_write,
        create_disposition=landing_create,
        **landing_kwargs,
    )
```

Add the import (with the other `sdfb_core` imports): `from sdfb_core.codegen import derive_bq_schema`.

The `dlq_sink` / `validation_runs_sink` / `rag_chunks_sink` blocks stay untouched.

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-beam packages/sdfb-tests
git add packages/sdfb-beam packages/sdfb-tests
git commit -m "feat(cli): --write_disposition + --create_if_not_exists on the landing sink (WS4 §6a/§6c)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `extract_table_schema` — live extraction callable (6b core)

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/ddl/extractor.py` (new function below `extract_ddl_metadata`)
- Modify: `packages/sdfb-beam/src/sdfb_beam/ddl/__init__.py` (export)
- Test: `packages/sdfb-tests/tests/unit/ddl/test_extractor.py` (append)

**Interfaces:**
- Consumes: `extract_ddl_metadata(project, dataset, table, *, timeout=DEFAULT_TIMEOUT, client=None) -> dict` (extractor.py:36, exists); `TableSchema` (`sdfb_core.contracts`).
- Produces (used by Task 3): `extract_table_schema(table_fqn: str, *, timeout: float = DEFAULT_TIMEOUT, client=None) -> TableSchema` — parses `project.dataset.table`, raises `ValueError` on malformed FQNs, returns a validated `TableSchema`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/sdfb-tests/tests/unit/ddl/test_extractor.py`. The FQN-validation tests need no client; the happy-path test reuses the SAME fake-client scaffolding that the existing `test_extractor_output_parses_as_table_schema` (line ~132) uses — read that test first and construct the fake client identically (do not invent a new fake):

```python
def test_extract_table_schema_rejects_malformed_fqn():
    from sdfb_beam.ddl import extract_table_schema

    for bad in ("dataset.table", "a.b.c.d", "a..c", "", "solo"):
        with pytest.raises(ValueError, match="project.dataset.table"):
            extract_table_schema(bad)


def test_extract_table_schema_returns_validated_schema():
    from sdfb_beam.ddl import extract_table_schema

    # Reuse the exact fake-client construction from
    # test_extractor_output_parses_as_table_schema — same fields, same
    # patched query behavior — then:
    schema = extract_table_schema("proj.ds.tbl", client=fake_client)
    assert schema.fqn  # a real TableSchema, not a dict
    assert [c.name for c in schema.columns]  # columns materialized
```

(If the existing test builds its fake client inline, extract that construction into a small module-level helper `_fake_extraction_client()` used by both tests — a mechanical refactor of the existing test, with its assertions unchanged.)

Also add `import pytest` to the test module imports if not already present.

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/ddl/test_extractor.py -q`
Expected: FAIL — `ImportError: cannot import name 'extract_table_schema'`.

- [ ] **Step 3: Implement**

In `extractor.py`, add below `extract_ddl_metadata` (and add `from sdfb_core.contracts import TableSchema` to the module imports):

```python
def extract_table_schema(
    table_fqn: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    client: bigquery.Client | None = None,
) -> TableSchema:
    """Live-extract a validated ``TableSchema`` for ``project.dataset.table``
    (WS4 §6b).

    The launch-time twin of the offline extract → stage → ``--ddl_uri``
    flow: same extractor, no GCS artifact. Runs at graph-construction time
    on the driver — the same lifecycle stage as the live reference read.
    """
    parts = table_fqn.split(".")
    if len(parts) != 3 or not all(parts):
        raise ValueError(
            f"table_fqn must be 'project.dataset.table', got {table_fqn!r}"
        )
    project, dataset, table = parts
    metadata = extract_ddl_metadata(
        project, dataset, table, timeout=timeout, client=client
    )
    return TableSchema.model_validate(metadata)
```

(If `extractor.py` type-hints `bigquery.Client` only under `TYPE_CHECKING` or via string annotations, match the file's existing convention for the `client` parameter annotation exactly.)

Add `extract_table_schema` to `ddl/__init__.py`'s imports and `__all__`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/ddl -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-beam packages/sdfb-tests
git add packages/sdfb-beam packages/sdfb-tests
git commit -m "feat(ddl): extract_table_schema — live TableSchema from INFORMATION_SCHEMA (WS4 §6b)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `--ddl_uri` optional with live-extraction precedence (6b CLI)

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (`--ddl_uri` arg ~line 68; new `resolve_table_schema`; `main()` ~line 277)
- Test: `packages/sdfb-tests/tests/unit/cli/test_run_pipeline.py` (append)

**Interfaces:**
- Consumes: `extract_table_schema` (Task 2), `load_ddl(ddl_uri) -> TableSchema` (exists, run_pipeline.py:186).
- Produces (used by Task 4): argparse `ddl_uri` default `""` (no longer required); `resolve_table_schema(ddl_uri: str, reference_table: str) -> TableSchema` with milestones `ddl_loaded_from_uri` / `ddl_live_extracted`.

- [ ] **Step 1: Write the failing tests**

Append to `test_run_pipeline.py`:

```python
def _args_without_ddl_uri() -> list[str]:
    args = _common_args()
    i = args.index("--ddl_uri")
    return args[:i] + args[i + 2:]


def test_parse_args_ddl_uri_optional_defaults_empty():
    args, beam_argv = parse_args(_args_without_ddl_uri())
    assert args.ddl_uri == ""
    assert beam_argv == []


def test_resolve_table_schema_prefers_explicit_uri(monkeypatch):
    from sdfb_beam.cli import run_pipeline as rp

    sentinel = object()
    monkeypatch.setattr(rp, "load_ddl", lambda uri: sentinel)
    live_calls: list[str] = []
    monkeypatch.setattr(
        "sdfb_beam.ddl.extract_table_schema",
        lambda fqn: live_calls.append(fqn),
    )
    assert rp.resolve_table_schema("gs://b/d.json", "p.d.t") is sentinel
    assert live_calls == []  # precedence: pin wins, live never touched


def test_resolve_table_schema_live_extracts_when_uri_empty(monkeypatch):
    from types import SimpleNamespace

    from sdfb_beam.cli import run_pipeline as rp

    sentinel = SimpleNamespace(columns=[1, 2], fqn="p.d.t")
    monkeypatch.setattr(
        "sdfb_beam.ddl.extract_table_schema", lambda fqn: sentinel
    )
    assert rp.resolve_table_schema("", "p.d.t") is sentinel
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli/test_run_pipeline.py -q`
Expected: FAIL — `_args_without_ddl_uri` parse fails (`--ddl_uri` still required) and `resolve_table_schema` doesn't exist.

- [ ] **Step 3: Implement in `run_pipeline.py`**

Change the `--ddl_uri` argument to:

```python
    p.add_argument("--ddl_uri", default="",
                   help="gs:// or local path to _ddl.json. OPTIONAL "
                        "(WS4 §6b): empty = live INFORMATION_SCHEMA "
                        "extraction from --reference_table at "
                        "graph-construction time. An explicit URI is the "
                        "pin/air-gap escape hatch and always wins.")
```

Add below `load_ddl`:

```python
def resolve_table_schema(ddl_uri: str, reference_table: str) -> TableSchema:
    """WS4 §6b precedence: explicit ``--ddl_uri`` (pin/air-gap) > live
    INFORMATION_SCHEMA extraction from the source table."""
    if ddl_uri:
        logger.info("Loading DDL from %s", ddl_uri)
        schema = load_ddl(ddl_uri)
        log_milestone("ddl_loaded_from_uri", uri=ddl_uri)
        return schema
    logger.info(
        "No --ddl_uri; live-extracting schema from %s", reference_table
    )
    from sdfb_beam.ddl import extract_table_schema

    schema = extract_table_schema(reference_table)
    log_milestone(
        "ddl_live_extracted",
        table=reference_table,
        columns=len(schema.columns),
    )
    return schema
```

(The deferred `from sdfb_beam.ddl import ...` keeps the BQ-client dependency out of fake-client laptop runs that pass an explicit local `--ddl_uri`, and makes the call-time attribute resolution monkeypatch-able.)

In `main()`, replace:

```python
    logger.info("Loading DDL from %s", args.ddl_uri)
    table_schema = load_ddl(args.ddl_uri)
```

with:

```python
    table_schema = resolve_table_schema(args.ddl_uri, args.reference_table)
```

(keep the following "Loaded schema for ..." log line unchanged).

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli packages/sdfb-tests/tests/unit/ddl -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-beam packages/sdfb-tests
git add packages/sdfb-beam packages/sdfb-tests
git commit -m "feat(cli): --ddl_uri optional — live schema extraction precedence (WS4 §6b)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Deploy-chain threading — Flex metadata, Composer DAG, workflow 3

**Files:**
- Modify: `docker/flex_template_metadata.json` (ddl_uri → optional; add `write_disposition`, `create_if_not_exists`)
- Modify: `composer/synthetic_beam_bigquery.py` (2 new Params, parameters dict, conditional `ddl_uri`, `WRITE_DISPOSITION` marker default)
- Modify: `.github/workflows/3_import_dag.yaml` (`ddl_file` input optional; new `write_disposition` input; resolve + sed steps)
- Test: `packages/sdfb-tests/tests/unit/public_cloud/test_tiers_matrix.py` (must stay green — it derives required/known params from the metadata JSON)

**Interfaces:**
- Consumes: argparse flags from Tasks 1/3 (`write_disposition`, `create_if_not_exists`, optional `ddl_uri`).
- Produces: the full launch chain can pass the new flags; an empty DDL marker means the DAG omits `ddl_uri` entirely and the launcher live-extracts. Task 5 relies on `ddl_uri` being `isOptional` in the metadata.

- [ ] **Step 1: Flex template metadata**

In `docker/flex_template_metadata.json`: add `"isOptional": true` to the existing `ddl_uri` parameter object (keep its regex — optional params are validated only when provided) and update its `helpText` to mention "empty/omitted = live INFORMATION_SCHEMA extraction from reference_table". Append two new parameter objects, mirroring the file's existing field style exactly:

```json
{
  "name": "write_disposition",
  "label": "Landing write disposition",
  "helpText": "append (default, WRITE_APPEND) or overwrite (WRITE_TRUNCATE). Landing table only; DLQ/quality/RAG tables always append.",
  "isOptional": true,
  "regexes": ["^(append|overwrite)$"]
},
{
  "name": "create_if_not_exists",
  "label": "Create landing table if missing",
  "helpText": "true/1/yes: CREATE_IF_NEEDED carrying the derived landing schema. Default false (CREATE_NEVER).",
  "isOptional": true,
  "regexes": ["^(true|false|1|0|yes|no)$"]
}
```

Verify: `python3 -c "import json; json.load(open('docker/flex_template_metadata.json')); print('ok')"` → `ok`.

- [ ] **Step 2: Composer DAG**

In `composer/synthetic_beam_bigquery.py`:

1. Near the other sed-marker constants (the block around lines 43-67 where `{{SDFB_DDL_URI}}` etc. are bound), add:

```python
# Landing write mode default — build-time marker (workflow 3 input), runtime
# overridable via the write_disposition DAG param on every trigger.
WRITE_DISPOSITION_DEFAULT = "{{WRITE_DISPOSITION}}"
# Empty ⇒ the DAG omits the ddl_uri Flex parameter entirely and the launcher
# live-extracts the schema from the source table (WS4 §6b).
_DDL_URI = "{{SDFB_DDL_URI}}"
```

(then use `_DDL_URI` wherever `"{{SDFB_DDL_URI}}"` appeared; if the marker constants live inline today, follow the file's existing pattern — e.g. `ENGINE_DEFAULT`.)

2. In `default_dag_params`, after the `seed` Param, add:

```python
    "write_disposition": Param(
        default=WRITE_DISPOSITION_DEFAULT,
        type="string",
        enum=["append", "overwrite"],
        description="Landing-table write mode; overwrite = WRITE_TRUNCATE. "
                    "Landing only — DLQ/quality/RAG tables always append.",
    ),
    "create_if_not_exists": Param(
        default="false",
        type="string",
        enum=["false", "true"],
        description="Create the landing table on first write "
                    "(CREATE_IF_NEEDED + derived schema).",
    ),
```

3. In the operator's `parameters` dict (lines ~284-310): replace the unconditional `"ddl_uri": "{{SDFB_DDL_URI}}",` entry by building the dict with a conditional spread, and add the two new entries:

```python
                "parameters": {
                    # Omitted entirely when the build left the DDL marker
                    # empty — the launcher then live-extracts (WS4 §6b). An
                    # empty-string value would fail the template's ddl_uri
                    # regex, so omission (not "") is the off state.
                    **({"ddl_uri": _DDL_URI} if _DDL_URI else {}),
                    "reference_table": "{{ params.table_fqn }}",
                    ...existing entries unchanged...,
                    "write_disposition": "{{ params.write_disposition }}",
                    "create_if_not_exists": "{{ params.create_if_not_exists }}",
                    "seed": "{{ params.seed }}",
                },
```

(`...existing entries unchanged...` means: keep every existing key exactly as-is; only the `ddl_uri` line changes form and the two new keys are appended.)

- [ ] **Step 3: Workflow 3 (`.github/workflows/3_import_dag.yaml`)**

1. `ddl_file` input (lines ~80-84): change `required: true` → `required: false`, `default: ""`, and extend the description with "Empty = live INFORMATION_SCHEMA extraction at launch (no GCS staging)."
2. Add a new input after `ddl_file`, mirroring the existing choice-input style (e.g. `gpu`):

```yaml
      write_disposition:
        description: "Landing-table write mode (append keeps today's behavior)"
        type: choice
        options: [append, overwrite]
        default: append
```

3. In the "Resolve deployment values" step (the `resolve_gcs` call that builds `DDL_URI`, ~line 245): guard for the empty input —

```bash
if [ -n "${{ inputs.ddl_file }}" ]; then
  DDL_URI="$(resolve_gcs "${{ inputs.ddl_file }}" "${DDLS_GCS_PREFIX}")"
else
  DDL_URI=""
fi
```

(adapt variable/step syntax to the file's existing style — read the surrounding step first; the semantic requirement is: empty input ⇒ `DDL_URI` empty, no resolve, no failure.)

4. In the marker-substitution (sed) step (~line 307): the `{{SDFB_DDL_URI}}` sed line must tolerate an empty `DDL_URI` (sed with an empty replacement is fine — verify the existing line doesn't fail on empty), and add a sibling sed line substituting `{{WRITE_DISPOSITION}}` with the `write_disposition` input, in the same style as the existing lines.
5. If the workflow has a marker-validation step that enumerates expected markers, add `WRITE_DISPOSITION` to its list.

- [ ] **Step 4: Verify**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud packages/sdfb-tests/tests/unit/cli -q`
Expected: all pass (`test_tiers_matrix.py` re-derives required params from the metadata — `ddl_uri` becoming optional must not break "every required param provided"; the two new optional params require no tiers.yaml entries).
Also: `python3 -c "import json; json.load(open('docker/flex_template_metadata.json')); print('ok')"` → `ok`, and `bash -n` is not applicable to workflow YAML — eyeball the sed/resolve edits against the file's existing quoting.

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check .
git add docker/flex_template_metadata.json composer/synthetic_beam_bigquery.py .github/workflows/3_import_dag.yaml
git commit -m "feat(deploy): thread write_disposition/create_if_not_exists + optional ddl_uri through template, DAG, workflow (WS4 §6a-c)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Personal-GCP E2E layer — drop `ddl_uri` staging from the happy path

**Files:**
- Modify: `public_cloud/deploy/gcp/tiers.yaml` (remove the two `ddl_uri:` entries, lines ~34 and ~41)
- Modify: `packages/sdfb-tests/tests/unit/public_cloud/test_render_tier.py` (the `params["ddl_uri"] == ...` assertion at ~line 28)

**Interfaces:**
- Consumes: `ddl_uri` marked `isOptional` in the metadata (Task 4) — the tiers-matrix drift guard (`test_every_required_template_param_is_provided`) only passes once that's true.
- Produces: the next personal-GCP E2E run exercises live extraction end-to-end (no `gs://.../ddl/*.json` staging).

- [ ] **Step 1: Update the test first**

In `test_render_tier.py`, replace the assertion `assert params["ddl_uri"] == "gs://db/ddl/citibike_trips_50k_ddl.json"` with:

```python
    # WS4 §6b: no DDL staging in the happy path — the launcher live-extracts.
    assert "ddl_uri" not in params
```

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_render_tier.py -q`
Expected: FAIL (tiers.yaml still renders `ddl_uri`).

- [ ] **Step 2: Remove the entries from `tiers.yaml`**

Delete the `ddl_uri: "gs://{DATAFLOW_BUCKET}/ddl/citibike_trips_50k_ddl.json"` and `ddl_uri: "gs://{DATAFLOW_BUCKET}/ddl/hacker_news_50k_ddl.json"` lines from the two table entries. Nothing else in the file changes.

- [ ] **Step 3: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud -q`
Expected: all pass (drift guard: tiers params ⊆ metadata params still holds; required-params check passes because `ddl_uri` is now optional).

- [ ] **Step 4: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-tests
git add public_cloud/deploy/gcp/tiers.yaml packages/sdfb-tests
git commit -m "feat(e2e): drop ddl_uri staging from tiers.yaml — live extraction is the happy path (WS4 §6b)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Full-baseline verification

**Files:** none new; fixes only if the baseline finds regressions.

- [ ] **Step 1: Full laptop baseline**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests -m "not gpu and not gcp" -q`
Expected: green (515 at branch point + ~10 new). Likeliest regression: any test that asserted `--ddl_uri` is required, or fixtures relying on the old hard-coded landing sink.

- [ ] **Step 2: Lint the whole repo**

Run: `uv run --no-sync ruff check .`
Expected: clean.

- [ ] **Step 3: Commit any regression fixes**

```bash
git add -u -- ':!.claude/settings.local.json'
git commit -m "test: WS4 baseline regression fixes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(Skip if nothing changed.)

**M4 follow-up (out of laptop scope):** a tier run WITHOUT staged DDL must succeed with the `ddl_live_extracted` milestone in worker logs; an `--write_disposition overwrite` re-run must land exactly `num_rows` rows (not cumulative); a `--create_if_not_exists true` run against a fresh `synthetic_data.<new_table>` must create it with the derived schema and zero manual `bq mk`. **WS5 follow-up:** `deployment_prerequisites.py` step 8b message ("OK — in-pipeline extraction") + docs sweep (RUN_PLAYBOOK, DEPLOYMENT_PREREQUISITES, workflow param tables, ROADMAP).
