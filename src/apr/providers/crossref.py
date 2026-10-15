"""Crossref: the registered record for a DOI, or a bibliographic search."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import quote, urlencode

from apr.match import (
    Candidate,
    canonical_url,
    full_name,
    normalise_doi,
    strip_markup,
    surname,
)
from apr.providers import http
from apr.providers.http import HttpOptions

API = "https://api.crossref.org/works"
SEARCH_ROWS = 5


def by_doi(
    doi: str, *, mailto: str | None = None, options: HttpOptions
) -> Candidate | None:
    """The registered work for this DOI; None when Crossref does not have it."""
    payload = _get_json(f"{API}/{quote(doi, safe='/')}", mailto=mailto, options=options)
    return _from_message(_message(payload))


def search(
    title: str,
    authors: Sequence[str] = (),
    *,
    mailto: str | None = None,
    options: HttpOptions,
) -> list[Candidate]:
    """Candidate works for a title and, when known, its first author."""
    params = {"query.bibliographic": title, "rows": str(SEARCH_ROWS)}
    first_author = surname(authors[0]) if authors else ""
    if first_author:
        params["query.author"] = first_author
    if mailto:
        params["mailto"] = mailto
    payload = _get_json(f"{API}?{urlencode(params)}", mailto=None, options=options)
    message = _message(payload)
    items = message.get("items") if isinstance(message, dict) else None
    if not isinstance(items, list):
        return []
    return [
        candidate
        for candidate in (
            _from_message(item) for item in items if isinstance(item, dict)
        )
        if candidate is not None
    ]


def _get_json(url: str, *, mailto: str | None, options: HttpOptions) -> Any:
    """GET, with the polite-pool address appended when the caller has one."""
    if mailto:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode({'mailto': mailto})}"
    try:
        return http.get_json(url, options=options)
    except http.HttpError as exc:
        if exc.not_found:
            return None  # unknown DOI: not an error, just no such record
        raise


def _message(payload: Any) -> Any:
    return payload.get("message") if isinstance(payload, dict) else None


def _from_message(work: Any) -> Candidate | None:
    if not isinstance(work, dict):
        return None
    doi = normalise_doi(work.get("DOI"))
    title = strip_markup(_first(work.get("title"))) or None
    if title is None and doi is None:
        return None
    return Candidate(
        title=title,
        authors=[name for name in map(_author, _list(work.get("author"))) if name],
        year=_year(work),
        venue=strip_markup(_first(work.get("container-title"))) or None,
        doi=doi,
        url=canonical_url(doi=doi),
        abstract=strip_markup(work.get("abstract")) or None,
    )


def _author(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    return full_name(entry.get("given"), entry.get("family")) or full_name(
        entry.get("name"), None
    )


def _year(work: dict[str, Any]) -> int | None:
    """The year of publication, not the year the record was deposited."""
    for key in ("issued", "published-print", "published", "published-online"):
        for part in _date_parts(work.get(key)):
            if isinstance(part, int) and 1000 <= part <= 2999:
                return part
    return None


def _date_parts(value: Any) -> list[Any]:
    if not isinstance(value, dict):
        return []
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
        return []
    return parts[0]


def _first(value: Any) -> str:
    return value[0] if isinstance(value, list) and value else ""


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
