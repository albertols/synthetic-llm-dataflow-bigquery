"""Relational metadata carried as JSON inside BigQuery descriptions.

The enterprise Terraform module cannot declare real PK/FK constraints, so
the table description carries a versioned contract (marker key ``"sdfb"``)
and column descriptions may carry per-column generation hints
(``"llm_prompt_constraint"``). See ADR 0021 and the 2026-08-05 spec (WS-A).

Table description example (prose around the object is fine)::

    {"sdfb": 1,
     "pk": ["ONE_FIELD", "TWO_FIELD"],
     "fk": [{"cols": ["ONE_FIELD"], "ref": "ds.b_table", "ref_cols": ["ONE_FIELD"]}],
     "identity": ["FIELD_THREE"]}
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from sdfb_core.contracts.description_json import (
    DescriptionJsonError,
    extract_embedded_json,
)

# Prompt steering, not schema enforcement: cap what a description can inject
# into an LLM prompt (2026-08-05 spec, C5 guardrails).
_MAX_CONSTRAINT_CHARS = 500

_TABLE_MARKER = "sdfb"
_CONSTRAINT_MARKER = "llm_prompt_constraint"


class ForeignKey(BaseModel):
    """One FK edge: this table's ``cols`` reference ``ref``'s ``ref_cols``."""

    model_config = ConfigDict(frozen=True)

    cols: tuple[str, ...]
    ref: str
    ref_cols: tuple[str, ...]

    @field_validator("cols", "ref_cols")
    @classmethod
    def _non_empty(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v or any(not c for c in v):
            raise ValueError("FK column lists must be non-empty strings")
        return v

    @field_validator("ref")
    @classmethod
    def _qualified(cls, v: str) -> str:
        if "." not in v:
            raise ValueError(
                f"fk.ref must be dataset-qualified (dataset.table), got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _same_arity(self) -> ForeignKey:
        if len(self.cols) != len(self.ref_cols):
            raise ValueError(
                f"fk cols/ref_cols arity mismatch: {self.cols} vs {self.ref_cols}"
            )
        return self


class RelationalContract(BaseModel):
    """The versioned table-level contract embedded in the description."""

    model_config = ConfigDict(frozen=True)

    sdfb: int
    pk: tuple[str, ...] = ()
    fk: tuple[ForeignKey, ...] = ()
    identity: tuple[str, ...] = ()


def parse_relational_contract(description: str | None) -> RelationalContract | None:
    """Contract from a table description, or None when unmarked.

    Raises :class:`DescriptionJsonError` for a marked object that is either
    unparseable JSON or schema-invalid — a half-parsed contract silently
    dropping an FK is worse than a loud stop.
    """
    obj = extract_embedded_json(description, _TABLE_MARKER)
    if obj is None:
        return None
    try:
        return RelationalContract.model_validate(obj)
    except ValidationError as exc:
        raise DescriptionJsonError(
            f"relational contract failed validation: {exc}"
        ) from exc


def parse_llm_prompt_constraint(description: str | None) -> str:
    """Rendered per-column prompt clause from a column description, or ``""``.

    Facade over :mod:`sdfb_core.contracts.prompt_constraint` (ADR 0024):
    the legacy string form renders byte-identically to its own normalized
    text; the structured object form renders its deterministic clause. A
    malformed marked object still raises via the extractor/validator.
    """
    from sdfb_core.contracts.prompt_constraint import (
        parse_prompt_constraint,
        render_prompt_clause,
    )

    pc = parse_prompt_constraint(description)
    return "" if pc is None else render_prompt_clause(pc)


__all__ = [
    "ForeignKey",
    "RelationalContract",
    "parse_llm_prompt_constraint",
    "parse_relational_contract",
]
