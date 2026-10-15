"""The evidence layers: what each source offers and what it wants to warn about."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Layer names, as documented in docs/schema.md.
SOURCE_FILENAME = "filename"
SOURCE_INFO = "pdf-info"
SOURCE_XMP = "pdf-xmp"
SOURCE_PAGE = "pdf-first-page"


@dataclass(frozen=True, slots=True)
class Hint:
    """One value offered for one field, with how it was obtained."""

    field: str
    value: Any
    method: str
    confidence: float
    detail: str | None = None


@dataclass(slots=True)
class LayerResult:
    """Everything one layer contributes: values offered and notes for the user."""

    source: str
    hints: list[Hint] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def offer(
        self,
        field: str,
        value: Any,
        *,
        method: str,
        confidence: float,
        detail: str | None = None,
    ) -> None:
        """Record a value; empty values are not evidence and are dropped."""
        if value is None:
            return
        if isinstance(value, str) and not value.strip():
            return
        if isinstance(value, (list, tuple)) and not value:
            return
        self.hints.append(
            Hint(
                field=field,
                value=value,
                method=method,
                confidence=confidence,
                detail=detail,
            )
        )
