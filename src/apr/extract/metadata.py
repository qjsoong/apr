"""Embedded metadata: the ``/Info`` dictionary and the XMP packet.

Embedded values are better than a file name and worse than the printed page:
they are written by the producer (sometimes from a template) and are frequently
truncated, empty or stale.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from apr.extract.layers import SOURCE_INFO, SOURCE_XMP, LayerResult
from apr.extract.normalize import (
    YEAR,
    find_arxiv,
    normalise_doi,
    normalise_title,
    plausible_title,
    split_author_list,
)

CONTENT_DATE = re.compile(r"(?:D:)?(?P<year>\d{4})")

_URL_OR_DOI_TAIL = re.compile(r"(?i)\s*[,;]?\s*(?:doi:|\bdoi\b|https?://|www\.).*$")


def info_layer(info: Mapping[str, Any]) -> LayerResult:
    """Offer the values of the document information dictionary."""
    layer = LayerResult(source=SOURCE_INFO)
    fields = {str(key).lower(): value for key, value in info.items()}

    title = _text(fields.get("/title"))
    if title and plausible_title(title):
        layer.offer(
            "title", normalise_title(title), method="info:Title", confidence=0.7
        )

    authors = split_author_list(_text(fields.get("/author")) or "")
    if authors:
        layer.offer("authors", authors, method="info:Author", confidence=0.7)

    doi = normalise_doi(
        " ".join(filter(None, (_text(fields.get(key)) for key in ("/doi", "/subject"))))
    )
    if doi is None:
        doi = normalise_doi(_text(fields.get("/crossrefdoi")) or "")
    if doi:
        layer.offer("doi", doi, method="info:doi", confidence=0.7)

    arxiv = find_arxiv(
        " ".join(_text(fields.get(key)) or "" for key in ("/arxivid", "/doi"))
    )
    if arxiv:
        layer.offer("arxiv_id", arxiv[0], method="info:arXivID", confidence=0.6)
        if arxiv[1]:
            layer.offer(
                "arxiv_version", arxiv[1], method="info:arXivID", confidence=0.6
            )

    subject = _clean_subject(_text(fields.get("/subject")) or "")
    venue = subject or _clean_subject(_text(fields.get("/journal")) or "")
    venue = venue or _clean_subject(_text(fields.get("/publicationname")) or "")
    venue = venue or _clean_subject(_text(fields.get("/book")) or "")
    if venue:
        layer.offer("venue", venue, method="info:Subject", confidence=0.6)

    abstract = _text(fields.get("/description-abstract"))
    if abstract and len(abstract) > 80:
        layer.offer(
            "abstract",
            " ".join(abstract.split()),
            method="info:Description-Abstract",
            confidence=0.55,
        )

    published = _first_year(fields, ("/published", "/date", "/created"))
    if published:
        layer.offer("year", published, method="info:PublicationDate", confidence=0.5)
    else:
        created = _content_year(_text(fields.get("/creationdate")) or "")
        if created:
            layer.offer("year", created, method="info:CreationDate", confidence=0.3)
    return layer


def xmp_layer(xmp: Any | None) -> LayerResult:
    """Offer the values of the XMP packet, when there is one."""
    layer = LayerResult(source=SOURCE_XMP)
    if xmp is None:
        return layer

    title = _xmp_string(getattr(xmp, "dc_title", None))
    if title and plausible_title(title):
        layer.offer(
            "title", normalise_title(title), method="xmp:dc:title", confidence=0.7
        )

    creators = _xmp_strings(getattr(xmp, "dc_creator", None))
    authors = [
        name for name in (split_author_list(c) for c in creators) for name in name
    ]
    if authors:
        layer.offer("authors", authors, method="xmp:dc:creator", confidence=0.8)

    description = _xmp_string(getattr(xmp, "dc_description", None)) or ""
    subjects = _xmp_strings(getattr(xmp, "dc_subject", None))
    identifiers = "; ".join(
        [_xmp_string(getattr(xmp, "dc_identifier", None)) or "", *subjects, description]
    )
    doi = normalise_doi(identifiers)
    if doi:
        layer.offer("doi", doi, method="xmp:dc:identifier", confidence=0.6)

    arxiv = find_arxiv(identifiers)
    if arxiv:
        layer.offer("arxiv_id", arxiv[0], method="xmp:dc:identifier", confidence=0.6)
        if arxiv[1]:
            layer.offer(
                "arxiv_version", arxiv[1], method="xmp:dc:identifier", confidence=0.6
            )

    venue = _clean_subject(description) or _clean_subject(_first(subjects) or "")
    if venue:
        layer.offer("venue", venue, method="xmp:dc:description", confidence=0.65)

    dates = _xmp_strings(getattr(xmp, "dc_date", None))
    year = _first_year_from(dates)
    if year:
        layer.offer("year", year, method="xmp:dc:date", confidence=0.6)
    return layer


def _text(value: Any) -> str | None:
    """pypdf hands metadata values over as str-like objects or bytes."""
    if value is None:
        return None
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    return text.strip() or None


def _xmp_string(value: Any) -> str | None:
    """XMP properties are plain strings, language maps, lists or bag dictionaries."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        for key in ("x-default", *value.keys()):
            entry = _xmp_string(value.get(key))
            if entry:
                return entry
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            entry = _xmp_string(item)
            if entry:
                return entry
        return None
    return str(value).strip() or None


def _xmp_strings(value: Any) -> list[str]:
    """Flatten an XMP property into the non-empty strings it holds."""
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [text for item in value.values() for text in _xmp_strings(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _xmp_strings(item)]
    text = _xmp_string(value)
    return [text] if text else []


def _first(values: list[str]) -> str | None:
    return values[0] if values else None


def _clean_subject(subject: str) -> str | None:
    """Strip DOIs and URLs a producer appended to ``/Subject`` or its like."""
    cleaned = _URL_OR_DOI_TAIL.sub("", subject)
    cleaned = re.sub(r"\s*[,;:]\s*$", "", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned if len(cleaned) >= 3 else None


def _first_year(fields: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        text = _text(fields.get(key))
        if not text:
            continue
        match = YEAR.search(text)
        if match:
            return int(match.group(1))
    return None


def _content_year(text: str) -> int | None:
    match = CONTENT_DATE.match(text)
    if not match:
        return None
    year = int(match.group("year"))
    return year if 1900 <= year <= 2100 else None


def _first_year_from(values: list[str]) -> int | None:
    for value in values:
        match = YEAR.search(value)
        if match:
            return int(match.group(1))
    return None
