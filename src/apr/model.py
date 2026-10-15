"""Shared record type for the identification pipeline.

Every stage contributes fields to one :class:`PaperRecord` and records where
the value came from. Conflicting values are resolved by confidence, so the
order in which stages run does not silently decide the outcome.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1

#: Fields a stage may contribute, in the order they appear in JSON output.
FIELDS: tuple[str, ...] = (
    "title",
    "authors",
    "year",
    "venue",
    "doi",
    "arxiv_id",
    "arxiv_version",
    "url",
    "abstract",
)

#: Relative weight of each field when computing overall confidence. Fields not
#: listed (venue, url, abstract, arxiv_version) carry no weight of their own.
FIELD_WEIGHTS: Mapping[str, float] = {
    "title": 0.45,
    "authors": 0.35,
    "year": 0.10,
    "doi": 0.05,
    "arxiv_id": 0.05,
}

#: A record with a confident title and an author list is "identified"; a title
#: alone, or a shakier title, is only "probable".
IDENTIFIED_TITLE_CONFIDENCE = 0.70

STATUS_IDENTIFIED = "identified"
STATUS_PROBABLE = "probable"
STATUS_UNIDENTIFIED = "unidentified"

SOURCE_OK = "ok"
SOURCE_NOT_FOUND = "not-found"
SOURCE_ERROR = "error"
SOURCE_SKIPPED = "skipped"


def _round(value: float) -> float:
    return round(float(value), 2)


@dataclass(slots=True)
class FieldSource:
    """Where the accepted value of one field came from."""

    source: str
    method: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "method": self.method,
            "confidence": _round(self.confidence),
        }


@dataclass(slots=True)
class Evidence:
    """One contribution considered for a field, accepted or not."""

    field: str
    value: Any
    source: str
    method: str
    confidence: float
    applied: bool
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "field": self.field,
            "value": self.value,
            "source": self.source,
            "method": self.method,
            "confidence": _round(self.confidence),
            "applied": self.applied,
        }
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass(slots=True)
class SourceAttempt:
    """One network lookup, successful or not. Offline runs record none."""

    source: str
    status: str
    url: str | None = None
    query: str | None = None
    candidates: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"source": self.source, "status": self.status}
        for key in ("query", "url", "candidates", "error"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


@dataclass(slots=True)
class PaperRecord:
    """What is known about one PDF, plus how it came to be known."""

    path: str | None = None
    size: int | None = None
    sha256: str | None = None
    pages: int | None = None

    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    arxiv_version: str | None = None
    url: str | None = None
    abstract: str | None = None

    provenance: dict[str, FieldSource] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    sources: list[SourceAttempt] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # -- contributing values -------------------------------------------------

    def set_file_info(
        self,
        *,
        path: str | None = None,
        size: int | None = None,
        sha256: str | None = None,
        pages: int | None = None,
    ) -> None:
        """Record facts about the input file itself (no confidence involved)."""
        if path is not None:
            self.path = path
        if size is not None:
            self.size = size
        if sha256 is not None:
            self.sha256 = sha256
        if pages is not None:
            self.pages = pages

    def set_field(
        self,
        name: str,
        value: Any,
        *,
        source: str,
        method: str,
        confidence: float,
        force: bool = False,
        detail: str | None = None,
        record: bool = True,
    ) -> bool:
        """Offer a value for a field; keep it only if it beats what is there.

        Returns True when the value was accepted. Empty values are ignored.
        Every considered non-empty value is appended to ``evidence``.
        """
        if name not in FIELDS:
            raise KeyError(f"unknown field: {name}")
        value = _clean(name, value)
        if value is None:
            return False

        current = self.provenance.get(name)
        keep_existing = (
            not force and current is not None and confidence <= current.confidence
        )
        if record:
            self.evidence.append(
                Evidence(
                    field=name,
                    value=value,
                    source=source,
                    method=method,
                    confidence=confidence,
                    applied=not keep_existing,
                    detail=detail
                    or (
                        f"kept earlier value from {current.source}"
                        if keep_existing and current is not None
                        else None
                    ),
                )
            )
        if keep_existing:
            return False

        setattr(self, name, value)
        self.provenance[name] = FieldSource(source, method, confidence)
        return True

    def add_source(self, attempt: SourceAttempt) -> None:
        self.sources.append(attempt)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    # -- views ---------------------------------------------------------------

    @property
    def confidence(self) -> float:
        """Weighted mean confidence over the weighted fields that are present."""
        total_weight = 0.0
        weighted = 0.0
        for name, weight in FIELD_WEIGHTS.items():
            source = self.provenance.get(name)
            if source is None or _empty(getattr(self, name)):
                continue
            total_weight += weight
            weighted += weight * source.confidence
        if total_weight == 0.0:
            return 0.0
        return weighted / total_weight

    @property
    def status(self) -> str:
        if not self.title:
            return STATUS_UNIDENTIFIED
        title_confidence = (
            self.provenance["title"].confidence if "title" in self.provenance else 0.0
        )
        if title_confidence >= IDENTIFIED_TITLE_CONFIDENCE and self.authors:
            return STATUS_IDENTIFIED
        return STATUS_PROBABLE

    def to_dict(
        self,
        *,
        include_evidence: bool = False,
        include_sources: bool = True,
        include_abstract: bool = True,
    ) -> dict[str, Any]:
        """Serialise to the documented schema (see docs/schema.md)."""
        from apr import __version__

        out: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "apr_version": __version__,
            "path": self.path,
            "status": self.status,
            "confidence": _round(self.confidence),
        }
        if self.size is not None:
            out["bytes"] = self.size
        if self.sha256:
            out["sha256"] = self.sha256
        if self.pages is not None:
            out["pages"] = self.pages
        for name in FIELDS:
            if name == "abstract" and not include_abstract:
                out[name] = None
                continue
            value = getattr(self, name)
            out[name] = (
                value if not _empty(value) else ([] if name == "authors" else None)
            )
        if self.warnings:
            out["warnings"] = list(self.warnings)
        out["provenance"] = {
            name: self.provenance[name].to_dict()
            for name in FIELDS
            if name in self.provenance
            and not _empty(getattr(self, name))
            and not (name == "abstract" and not include_abstract)
        }
        if include_sources:
            out["sources"] = [attempt.to_dict() for attempt in self.sources]
        if include_evidence:
            out["evidence"] = [item.to_dict() for item in self.evidence]
        return out


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _clean(name: str, value: Any) -> Any:
    """Normalise a value for storage; return None for anything empty."""
    if _empty(value):
        return None
    if name == "authors":
        seen: list[str] = []
        items: Iterable[Any] = value if isinstance(value, (list, tuple)) else [value]
        for item in items:
            text = " ".join(str(item).split())
            if text and text not in seen:
                seen.append(text)
        return seen or None
    if name == "year":
        try:
            year = int(str(value)[:4])
        except (TypeError, ValueError):
            return None
        return year if 1000 <= year <= 2999 else None
    if isinstance(value, str):
        return " ".join(value.split()) if name in ("title", "venue") else value.strip()
    return value
