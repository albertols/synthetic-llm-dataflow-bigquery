"""FK pools — parent synthetic key values for child-table generation.

Parent-first multi-table (ADR 0021, Option 1): a child table's FK columns
sample from the DISTINCT key values its parent has already LANDED in the
synthetic dataset — driver-side eager read, delivered to workers through
``GenerationContext.fk_pools`` exactly like reference rows.

The contract's ``fk.ref`` names the SOURCE-world ``dataset.table``; the
landed synthetic parent lives in the landing dataset under the same table
name, so the read targets ``{landing_dataset}.{ref table name}``.

v1 limitation, on record: composite FKs load aligned per-column pools, and
the engines draw each column INDEPENDENTLY — cross-column tuples are not
guaranteed to co-occur in the parent. Single-column FKs (the common case)
are exact. Joint tuple draws are the follow-up recorded in ROADMAP M2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sdfb_core.observability import log_milestone

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sdfb_core.contracts.relational import ForeignKey

_DEFAULT_LIMIT = 100_000


def parent_landing_fqn(ref: str, landing_dataset: str) -> str:
    """``ds.parent`` + ``project.synthetic_data`` → ``project.synthetic_data.parent``."""
    return f"{landing_dataset}.{ref.rsplit('.', 1)[-1]}"


def load_fk_pools(
    fks: tuple[ForeignKey, ...],
    landing_dataset: str,
    client=None,
    limit: int = _DEFAULT_LIMIT,
) -> dict[str, tuple]:
    """child column → tuple of parent key values, for every FK edge.

    ``client`` is a BigQuery client (injected for tests). Composite FKs
    issue ONE query per edge and slice the aligned columns out of it.
    """
    if not fks:
        return {}
    if client is None:  # pragma: no cover - GCP-only path
        from google.cloud import bigquery

        client = bigquery.Client()

    pools: dict[str, tuple] = {}
    for fk in fks:
        parent = parent_landing_fqn(fk.ref, landing_dataset)
        cols_sql = ", ".join(f"`{c}`" for c in fk.ref_cols)
        sql = (
            # Identifiers come from a validated contract, not user input.
            f"SELECT DISTINCT {cols_sql} FROM `{parent}` "
            f"WHERE {' AND '.join(f'`{c}` IS NOT NULL' for c in fk.ref_cols)} "
            f"LIMIT {int(limit)}"
        )
        rows = list(client.query(sql).result())
        for child_col, ref_col in zip(fk.cols, fk.ref_cols, strict=True):
            pools[child_col] = tuple(row[ref_col] for row in rows)
        log_milestone(
            "fk_pool_loaded",
            parent=parent,
            child_cols=",".join(fk.cols),
            values=len(rows),
        )
    return pools


__all__ = ["load_fk_pools", "parent_landing_fqn"]
