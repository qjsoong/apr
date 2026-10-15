"""DataCite: the DOI fallback for records Crossref does not hold.

DataCite registers datasets, software and repository deposits (Zenodo, Dryad,
institutional repositories). It is consulted only when the registries that own
journal and conference DOIs have nothing, and it never supplies a venue: its
``publisher`` field is the depositor, not a journal or conference.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from apr.match import (
    Candidate,
    canonical_url,
    full_name,
    invert_name,
    normalise_doi,
    strip_markup,
)
from apr.providers import http
from apr.providers.http import HttpOptions

API = "https://api.datacite.org/dois"


def by_doi(doi: str, *, options: HttpOptions) -> Candidate | None:
    """The DataCite record for this DOI."""
    try:
        payload = http.get_json(f"{API}/{quote(doi, safe='/')}", options=options)
    except http.HttpError as exc:
        if exc.not_found:
            return None
        raise
    data = payload.get("data") if isinstance(payload, dict) else None
    attributes = data.get("attributes") if isinstance(data, dict) else None
    return _from_attributes(attributes)


def _from_attributes(attributes: Any) -> Candidate | None:
    if not isinstance(attributes, dict):
        return None
    doi = normalise_doi(attributes.get("doi"))
    title = _title(attributes.get("titles"))
    if title is None and doi is None:
        return None
    return Candidate(
        title=title,
        authors=[name for name in map(_creator, _list(attributes, "creators")) if name],
        year=_year(attributes.get("publicationYear")),
        doi=doi,
        url=canonical_url(doi=doi),
        abstract=_abstract(attributes.get("descriptions")),
    )


def _title(titles: Any) -> str | None:
    """The title DataCite marks as main, or the first one it lists."""
    entries = [entry for entry in (titles or []) if isinstance(entry, dict)]
    main = [entry for entry in entries if entry.get("titleType") == "Main"] or entries
    for entry in main:
        title = strip_markup(entry.get("title"))
        if title:
            return title
    return None


def _creator(entry: Any) -> str | None:
    """``'Döbeli Honegger, Beat'`` -> ``'Beat Döbeli Honegger'``."""
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if isinstance(name, str) and name.strip():
        return invert_name(name)
    return full_name(entry.get("givenName"), entry.get("familyName"))


def _abstract(descriptions: Any) -> str | None:
    entries = [entry for entry in (descriptions or []) if isinstance(entry, dict)]
    for entry in entries:
        if entry.get("descriptionType") in (None, "Abstract"):
            text = strip_markup(entry.get("description"))
            if text:
                return text
    return None


def _year(value: Any) -> int | None:
    return value if isinstance(value, int) and 1000 <= value <= 2999 else None


def _list(attributes: dict[str, Any], key: str) -> list[Any]:
    value = attributes.get(key)
    return value if isinstance(value, list) else []
