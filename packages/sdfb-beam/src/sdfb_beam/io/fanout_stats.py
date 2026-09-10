"""SOURCE fan-out statistics for a driven child (design 2026-09-10, ADR 0036).

Per driving edge, measured driver-side on the SOURCE child table: the
histogram of children per parent tuple (zero bucket from the source
parent's distinct tuple count) and the joint PK-completing cells. One
scan of the FK + cell columns; cached in ``synthetic_data_quality
.fk_fanout_stats`` by (source child, edge cols, model sha) so a
re-launch pays nothing. A 5k-row sample cannot measure this: it almost
never holds two rows of one parent (design §10).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sdfb_core.observability import log_milestone

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Rarely-typed control char used as a tuple-key separator in the joint
# COUNT(DISTINCT CONCAT(...)) below — chosen because it cannot appear in a
# CAST(... AS STRING) value from any column BigQuery lets us read here.
# The SQL literal carries the ESCAPE (`'\\x1f'`, four printable chars), not
# a raw 0x1F byte: the query text is logged, copied into the BigQuery
# console and diffed by humans, and an invisible control byte in it
# survives none of that intact. BigQuery parses `\\x1f` in a string literal
# to the same character.
_TUPLE_SEP = "\\x1f"

# Percentile cut points for the fan-out milestone below.
_P50 = 0.5
_P95 = 0.95


def _validated(cols: tuple[str, ...]) -> tuple[str, ...]:
    for c in cols:
        if not _IDENTIFIER.match(c):
            raise ValueError(f"not a BigQuery column name: {c!r}")
    return cols


def _cols(cols: tuple[str, ...]) -> str:
    return ", ".join(f"`{c}`" for c in _validated(cols))


def _distinct_key_sql(cols: tuple[str, ...]) -> str:
    """``COUNT(DISTINCT ...)`` over one or several columns as a tuple key.

    A single column skips the CONCAT entirely; two or more are joined by a
    single-quoted separator literal with no trailing separator, e.g.
    ``COUNT(DISTINCT CONCAT(CAST(`c1` AS STRING), '\\x1f', CAST(`c2` AS
    STRING)))``. Callers pass already-``_validated`` columns — this does
    not re-validate.
    """
    casts = [f"CAST(`{c}` AS STRING)" for c in cols]
    if len(casts) == 1:
        return f"COUNT(DISTINCT {casts[0]})"
    joined = f", '{_TUPLE_SEP}', ".join(casts)
    return f"COUNT(DISTINCT CONCAT({joined}))"


def _rows(job: Any) -> list[dict]:
    return [dict(r) if not isinstance(r, dict) else r for r in job.result()]


def measure_fanout(
    *,
    source_child: str,
    child_cols: tuple[str, ...],
    source_parent: str,
    ref_cols: tuple[str, ...],
    cell_cols: tuple[str, ...],
    client: Any = None,
    edge: str = "",
) -> dict:
    """``{"histogram", "cells", "parents", "children"}`` from three
    GROUP BY queries; ``cells`` is None without cell columns.

    ``edge`` labels the ``fk_fanout_source_orphans`` warning below and is
    otherwise unused.
    """
    if client is None:  # pragma: no cover - GCP-only path
        from google.cloud import bigquery

        client = bigquery.Client()
    child_sql = (
        f"SELECT n AS k, COUNT(*) AS parents FROM (SELECT {_cols(child_cols)}, "
        f"COUNT(*) AS n FROM `{source_child}` GROUP BY {_cols(child_cols)}) GROUP BY n"
    )
    histogram = {int(r["k"]): int(r["parents"]) for r in _rows(client.query(child_sql))}
    ref = _validated(ref_cols)
    parent_sql = (
        f"SELECT {_distinct_key_sql(ref)} AS parents FROM `{source_parent}` WHERE "
        + " AND ".join(f"`{c}` IS NOT NULL" for c in ref)
    )
    (parent_row,) = _rows(client.query(parent_sql))
    parents = int(parent_row["parents"])
    with_children = sum(histogram.values())
    if with_children > parents:
        # The SOURCE child holds tuples its own parent does not (the
        # source's FK is not enforced, or the two tables were snapshotted
        # at different times). The zero bucket is then unmeasurable — it
        # is clamped to 0, not negative — and `children / parents` is an
        # UPPER bound on the true mean, which sizes the whole driven
        # child. Announced, never inferred from a suspicious ratio.
        log_milestone(
            "fk_fanout_source_orphans",
            level=logging.WARNING,
            edge=edge,
            child_tuples=with_children,
            parent_tuples=parents,
            note=(
                "child tuples without a parent in the source; the zero "
                "bucket is unmeasurable and the mean fan-out is an upper "
                "bound"
            ),
        )
    # The child GROUP BY above can never emit k=0 (COUNT(*) over an actual
    # group is always >= 1), so histogram.get(0, 0) is always 0 here — this
    # line's only job is filling the zero bucket from the parent count.
    histogram[0] = max(0, parents - with_children) + histogram.get(0, 0)
    children = sum(k * n for k, n in histogram.items())
    cells = None
    if cell_cols:
        cell_sql = (
            f"SELECT {_cols(cell_cols)}, COUNT(*) AS n FROM `{source_child}` "
            f"GROUP BY {_cols(cell_cols)}"
        )
        rows = _rows(client.query(cell_sql))
        cells = {
            "cols": list(cell_cols),
            "rows": [[r[c] for c in cell_cols] for r in rows],
            "counts": [float(r["n"]) for r in rows],
        }
    return {
        "histogram": {str(k): n for k, n in sorted(histogram.items())},
        "cells": cells,
        "parents": parents,
        "children": children,
    }


def fanout_payload(measured: dict, driving_cols: tuple[str, ...], exact_cells: bool) -> dict:
    """The ``FanoutPlan`` payload shape (Task 1)."""
    return {
        "driving_cols": list(driving_cols),
        "histogram": dict(measured["histogram"]),
        "cells": measured.get("cells"),
        "exact_cells": bool(exact_cells),
    }


def log_fanout_measured(edge: str, measured: dict, *, source: str) -> None:
    """One ``fk_fanout_measured`` milestone: p50/p95 over PARENTS (the
    histogram's counts), mean children-per-parent, and the zero share."""
    hist = {int(k): n for k, n in measured["histogram"].items()}
    total = sum(hist.values()) or 1
    order = sorted(hist)
    cum = 0
    p50 = p95 = order[-1]
    for k in order:
        cum += hist[k]
        if cum / total >= _P50 and p50 == order[-1]:
            p50 = k
        if cum / total >= _P95:
            p95 = k
            break
    log_milestone(
        "fk_fanout_measured",
        edge=edge,
        parents=measured["parents"],
        children=measured["children"],
        mean=round(measured["children"] / max(1, measured["parents"]), 4),
        p50=p50,
        p95=p95,
        max=order[-1],
        zero_share=round(hist.get(0, 0) / total, 4),
        source=source,
    )


class BigQueryFanoutStatsStore:
    """Cache of measured fan-out payloads in ``fk_fanout_stats``, keyed by
    (source child table, edge cols, model sha). Mirrors
    ``BigQuerySourceStatsStore`` — lazy client, pickle-safe."""

    def __init__(self, table_fqn: str, *, client: Any = None) -> None:
        self.table_fqn = table_fqn
        self._client = client  # injectable for tests; lazy real client

    def __getstate__(self) -> dict:
        # The lazy client is a CACHE, not state — google.cloud clients
        # refuse to pickle (see io/stats_store.py, io/source_values.py).
        state = self.__dict__.copy()
        state["_client"] = None
        return state

    def _bq(self):
        if self._client is None:  # pragma: no cover - GCP-only path
            from google.cloud import bigquery

            self._client = bigquery.Client()
        return self._client

    def get(self, source_table: str, edge_cols: tuple[str, ...], model_sha: str) -> dict | None:
        """Most recent cached payload for this (table, edge, model), or
        None on a cache miss."""
        from google.cloud import bigquery

        sql = (
            f"SELECT payload FROM `{self.table_fqn}` WHERE source_table = @t "
            f"AND edge_cols = @e AND model_sha = @s ORDER BY measured_at DESC LIMIT 1"
        )
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("t", "STRING", source_table),
                bigquery.ScalarQueryParameter("e", "STRING", ",".join(edge_cols)),
                bigquery.ScalarQueryParameter("s", "STRING", model_sha),
            ]
        )
        rows = _rows(self._bq().query(sql, job_config=job_config))
        return json.loads(rows[0]["payload"]) if rows else None

    def put(
        self, source_table: str, edge_cols: tuple[str, ...], model_sha: str, payload: dict
    ) -> None:
        """Append one measured payload via a LOAD job (never streaming —
        the CLAUDE.md batch rule)."""
        from google.cloud import bigquery

        row = {
            "source_table": source_table,
            "edge_cols": ",".join(edge_cols),
            "model_sha": model_sha,
            "measured_at": datetime.now(UTC).isoformat(),
            "payload": json.dumps(payload, separators=(",", ":"), default=str),
        }
        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
        self._bq().load_table_from_json([row], self.table_fqn, job_config=job_config).result()


__all__ = ["BigQueryFanoutStatsStore", "fanout_payload", "log_fanout_measured", "measure_fanout"]
