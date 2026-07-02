---
name: reference-data
description: Recipe for the live BQ SELECT reference read + the canonical digest that captures which reference rows a job saw, for provenance. Load when working on `sdfb_beam/io/bq_read.py` or anything that consumes reference rows.
---

# Skill — reference data + provenance digest

Reference rows are pulled live every job (no caching). The digest captures *what was pulled* so a run can be re-traced even if the underlying table has changed.

## Read

```python
ref_rows = (
    p
    | "ReadReference" >> beam.io.ReadFromBigQuery(
        query=f"SELECT * FROM `{args.table}` LIMIT {args.reference_rows}",
        use_standard_sql=True,
    )
)
```

Default N = 10_000 (override via `--reference_rows`). PII columns are NOT masked in M1 (DEV-only assumption); revisit before any PRD use.

REF: https://beam.apache.org/releases/pydoc/current/apache_beam.io.gcp.bigquery.html

## Canonical digest

```python
def compute_canonical_digest(rows: Iterable[dict]) -> str:
    """SHA-256 of canonical-encoded reference rows.

    Sorting + hashing makes this associative-by-construction, so it works
    as the extract step of a CombineFn.
    """
    sorted_rows = sorted(
        rows,
        key=lambda r: json.dumps(r, sort_keys=True, default=str),
    )
    h = hashlib.sha256()
    for r in sorted_rows:
        h.update(json.dumps(r, sort_keys=True, default=str).encode())
    return h.hexdigest()
```

Use `beam.CombineGlobally(compute_canonical_digest)` or wrap as a `CombineFn` if memory becomes a concern at higher N.

## Provenance fields written to `validation_runs`

| Column | Type | Source |
|---|---|---|
| `reference_digest` | STRING | the SHA-256 above |
| `reference_row_count` | INT64 | `len(rows)` |
| `reference_query_id` | STRING | Beam's BQ read job id |
| `reference_pulled_at` | TIMESTAMP | wall-clock at read start |
| `reference_table` | STRING | the source table FQN |

## Live-SELECT trade-off (worth flagging)

Non-deterministic across runs — two consecutive jobs may see different reference rows. Implications:
- **SDMetrics fidelity (M2)** must compare digest before comparing scores — different reference ⇒ scores incomparable.
- **Debug repro** uses the digest as the lookup key. If a row generated yesterday looked different, check the digest first.
- **CI determinism** uses a fixture instead of live SELECT (the `FakeModelClient` reads a frozen reference parquet for deterministic tests).

## Current implementation

| Concern | File | Tests |
|---|---|---|
| Driver-side BQ read (`SELECT … LIMIT N`) | `packages/sdfb-beam/src/sdfb_beam/io/bq_sources.py` | `tests/unit/io/test_bq_sources.py` |
| Canonical SHA-256 digest | `packages/sdfb-beam/src/sdfb_beam/io/digest.py` | covered via pipeline integration test |

ADR: [`docs/adr/0005-live-select-reference-data.md`](../../docs/adr/0005-live-select-reference-data.md).

## References

- BigQueryIO Read: https://beam.apache.org/releases/pydoc/current/apache_beam.io.gcp.bigquery.html
- BQ JSON schema file: https://docs.cloud.google.com/bigquery/docs/schemas#creating_a_JSON_schema_file
