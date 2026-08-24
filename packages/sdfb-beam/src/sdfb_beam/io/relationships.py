"""Load relationship models from a directory, a file, or GCS (ADR 0032).

`config/relationships/` ships inside the flex-template image, so the
default needs no upload and no `bq update`. Pointing
``--relationships_uri`` at a `gs://` path overrides the packaged models
for that launch — a relationship change is then a file upload, not an
image rebuild and not a production metadata edit.

Parsing and every graph question live in
:mod:`sdfb_core.contracts.relationships` (pure Python, no Beam); this
module only turns a URI into ``(source, text)`` pairs.
"""

from __future__ import annotations

import logging

from apache_beam.io.filesystems import FileSystems
from sdfb_core.contracts.relationships import (
    RelationshipError,
    RelationshipRegistry,
)
from sdfb_core.observability import log_milestone

_SUFFIXES = (".yaml", ".yml")


def _patterns(uri: str) -> list[str]:
    """A file URI matches itself; anything else is treated as a folder."""
    if uri.endswith(_SUFFIXES):
        return [uri]
    base = uri.rstrip("/")
    return [f"{base}/*{suffix}" for suffix in _SUFFIXES]


def load_relationship_registry(uri: str) -> RelationshipRegistry:
    """Every model file under ``uri``, validated into one registry.

    An empty or missing location is a legitimate state — "no
    relationships declared anywhere" — and yields an empty registry, so
    single-table generation needs no config at all. A file that EXISTS
    but does not parse is a loud stop: a half-read model would silently
    generate the wrong relational shape.
    """
    if not uri:
        return RelationshipRegistry()
    paths: list[str] = []
    for pattern in _patterns(uri):
        try:
            for match in FileSystems.match([pattern]):
                paths.extend(
                    metadata.path for metadata in match.metadata_list
                )
        except Exception:  # pragma: no cover - filesystem-dependent
            continue
    sources: list[tuple[str, str]] = []
    for path in sorted(set(paths)):
        try:
            with FileSystems.open(path) as handle:
                sources.append((path, handle.read().decode("utf-8")))
        except Exception as exc:
            raise RelationshipError(
                f"{path}: could not be read ({type(exc).__name__}: {exc}) — "
                f"refusing to launch with a partially-loaded relational "
                f"model"
            ) from exc
    if not sources:
        log_milestone(
            "relationships_absent",
            uri=uri,
            note="no model files found — every table generates in "
            "isolation with PK/identity from the CLI flags",
        )
        return RelationshipRegistry()
    registry = RelationshipRegistry.from_sources(sources)
    log_milestone(
        "relationships_loaded",
        uri=uri,
        files=len(sources),
        models=",".join(m.model for m in registry.models),
        tables=sum(len(m.tables) for m in registry.models),
        sha=registry.sha12(),
        level=logging.INFO,
    )
    return registry


__all__ = ["load_relationship_registry"]
