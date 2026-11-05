"""Module-wide exceptions."""
from __future__ import annotations


class MissingInputError(KeyError):
    """An input required by a routine is missing. CLAUDE.md rule 8: never fall back silently."""

    def __init__(self, field: str, where: str = "") -> None:
        self.field = field
        self.where = where
        super().__init__(f"missing input {field!r}" + (f" (required by {where})" if where else ""))


class RecordValidationError(ValueError):
    """Schema or provenance validation failure. `errors` holds (field path, message) pairs."""

    def __init__(self, record_id: str, errors: list[tuple[str, str]]) -> None:
        self.record_id = record_id
        self.errors = errors
        lines = "\n".join(f"  {p}: {m}" for p, m in errors)
        super().__init__(f"record {record_id!r} failed validation:\n{lines}")
